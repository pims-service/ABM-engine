"""The harness: score the qualification step against labelled cases, and compare two runs.

A case holds a campaign's rules, a company's facts, its dated signals and the **expected outcome**
(ICP fit, recommendation, trigger yes or no). The outcome is judged in the same order as the
analysis: the model's verdict (``FitVerdict``), then the campaign's hard rules
(``engine.rules.apply_rules``), with the trigger decided separately from the signals by their
freshness windows. ICP fit and trigger never influence each other (Brief section 2).

Two modes:

* **recorded** (default, offline, deterministic): the model's answer for each case is stored in the
  case file (``model_output``). This checks everything around the model: the answer still fits the
  schema, the hard rules still catch what they must, the trigger windows still decide the trigger.
  The committed answers are written by hand as a model that follows the instructions (marked
  ``model_output_source: "scripted"``) until ``--record`` replaces them with real answers.
* **live**: the client's AI connection answers each case, so the result says how well the real
  prompt and model agree with the labels. ``--record`` stores those answers in the case files.

Nothing here touches the database: a campaign is an unsaved ``CampaignProfile``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from apps.campaigns.models import CampaignProfile
from apps.integrations.llm import pydantic_schema, structured_call
from apps.research.engine.prompts import ASSESS_VERSION, SCHEMA_VERSION, assessment_prompt
from apps.research.engine.rules import Verdict, apply_rules
from apps.research.engine.schemas import FitVerdict
from apps.research.models import Signal
from apps.research.windows import expiry_for, usable_windows

HARNESS_VERSION = 1
CASES_DIR = Path(__file__).parent / "cases"
FINGERPRINT_FILE = Path(__file__).parent / "prompt_fingerprint.json"
#: The checked fields of an outcome.
FIELDS = ("fit", "recommendation", "trigger")


@dataclass
class CaseResult:
    id: str
    tags: list[str]
    expected: dict[str, Any]
    got: dict[str, Any] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    model_output_source: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass
class Report:
    mode: str
    prompt_version: str
    schema_version: str
    model: str
    cases: list[CaseResult]

    def summary(self) -> dict[str, Any]:
        total = len(self.cases)
        body: dict[str, Any] = {
            "cases": total,
            "passed": sum(1 for c in self.cases if c.passed),
        }
        for name in FIELDS:
            right = sum(1 for c in self.cases if c.got.get(name) == c.expected.get(name))
            body[f"{name}_correct"] = right
        body["pass_rate"] = round(body["passed"] / total, 4) if total else 0.0
        # Agreement per expected ICP fit class: how many cases of each class came out right.
        by_class: dict[str, dict[str, int]] = {}
        for case in self.cases:
            entry = by_class.setdefault(str(case.expected.get("fit")), {"cases": 0, "correct": 0})
            entry["cases"] += 1
            entry["correct"] += int(case.got.get("fit") == case.expected.get("fit"))
        body["fit_by_class"] = by_class
        return body

    def to_json(self) -> dict[str, Any]:
        return {
            "harness_version": HARNESS_VERSION,
            "mode": self.mode,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "model": self.model,
            "summary": self.summary(),
            "cases": [asdict(case) for case in self.cases],
        }


# ------------------------------------------------------------------ the cases


def load_cases(
    directory: Path = CASES_DIR, only: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    """Every case file (sorted by id). ``only`` limits it to the named ids."""
    wanted = set(only or ())
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]
    if wanted:
        cases = [c for c in cases if c["id"] in wanted]
    return cases


def profile_of(case: Mapping[str, Any]) -> CampaignProfile:
    """The campaign's rules as an unsaved profile (the engine only reads its rule fields)."""
    return CampaignProfile(**case["campaign"])


def signals_of(case: Mapping[str, Any]) -> list[Signal]:
    return [
        Signal(
            type=s["type"],
            event_date=date.fromisoformat(s["event_date"]),
            evidence=s["evidence"],
        )
        for s in case.get("signals", [])
    ]


def as_of_of(case: Mapping[str, Any]) -> datetime:
    """The day the case is judged on, at noon UTC."""
    day = date.fromisoformat(case["as_of"])
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def trigger_yes(case: Mapping[str, Any]) -> bool:
    """Trigger Yes when a signal is still fresh under the campaign's windows at the case date."""
    windows = usable_windows(case.get("trigger_windows"))
    moment = as_of_of(case)
    return any(expiry_for(s.type, s.event_date, windows) > moment for s in signals_of(case))


def known_facts(case: Mapping[str, Any]) -> dict[str, object]:
    return {k: v for k, v in case["company"].items() if k != "name" and v not in (None, "")}


# ------------------------------------------------------------------ scoring


def evaluate_case(case: Mapping[str, Any], model_output: Any) -> CaseResult:
    """Judge one case from a model answer (the decoded JSON the model returned)."""
    result = CaseResult(
        id=case["id"],
        tags=list(case.get("tags", [])),
        expected=dict(case["expected"]),
        model_output_source=str(case.get("model_output_source", "")),
    )
    try:
        verdict = FitVerdict(**model_output) if isinstance(model_output, Mapping) else None
    except ValidationError as exc:
        paths = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        result.failures.append(f"the model answer does not fit the schema ({', '.join(paths)})")
        verdict = None
    if verdict is None and not result.failures:
        result.failures.append("the model answer is not an object")
    company = case["company"]
    final: Verdict | None = None
    if verdict is not None:
        final = apply_rules(
            Verdict(
                fit=verdict.fit,
                recommendation=verdict.recommendation,
                reasons=list(verdict.reasons),
                concerns=list(verdict.concerns),
            ),
            profile_of(case),
            industry=company.get("industry"),
            description=company.get("description"),
            classification=company.get("classification"),
            employee_count=company.get("employee_count"),
        )
    result.got = {
        "fit": final.fit if final else None,
        "recommendation": final.recommendation if final else None,
        "trigger": trigger_yes(case),
    }
    for name in FIELDS:
        if result.got[name] != result.expected.get(name):
            result.failures.append(
                f"{name}: expected {result.expected.get(name)!r}, got {result.got[name]!r}"
            )
    return result


def run_recorded(cases: list[dict[str, Any]]) -> Report:
    """Score every case with the model answer stored in its file."""
    results = [evaluate_case(case, case.get("model_output")) for case in cases]
    return Report("recorded", ASSESS_VERSION, SCHEMA_VERSION, "recorded", results)


def ask_model(adapter: Any, case: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """The live model's answer for ``case`` (decoded JSON) and the model name."""
    prompt = assessment_prompt(
        profile_of(case), case["company"]["name"], known_facts(case), signals_of(case)
    )
    answer = structured_call(adapter, prompt, pydantic_schema(FitVerdict), max_tokens=1500)
    data = answer.data
    model = answer.usage.model or str(getattr(adapter, "config", {}).get("model", "")) or "unknown"
    return (data.model_dump() if data is not None else {}), model


def run_live(
    cases: list[dict[str, Any]],
    adapter: Any,
    on_answer: Callable[[dict[str, Any], dict[str, Any], str], None] | None = None,
) -> Report:
    """Score every case with the live model. ``on_answer(case, output, model)`` sees each answer
    (the command uses it to record them). A provider error fails that case, not the run."""
    results: list[CaseResult] = []
    model_name = "unknown"
    for case in cases:
        try:
            output, model_name = ask_model(adapter, case)
        except Exception as exc:  # one bad call must not end the run
            failed = evaluate_case(case, {})
            failed.failures.insert(0, f"the model call failed ({type(exc).__name__})")
            results.append(failed)
            continue
        if on_answer is not None:
            on_answer(case, output, model_name)
        results.append(evaluate_case(case, output))
    return Report("live", ASSESS_VERSION, SCHEMA_VERSION, model_name, results)


# ------------------------------------------------------------------ comparing two runs


def compare(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """What changed between two reports (the dicts ``Report.to_json`` makes): cases that started or
    stopped passing, cases whose outcome changed, cases added or removed, and the metric deltas."""
    before = {c["id"]: c for c in previous["cases"]}
    after = {c["id"]: c for c in current["cases"]}
    regressions, fixed, changed = [], [], []
    for case_id in sorted(before.keys() & after.keys()):
        was, now = before[case_id], after[case_id]
        was_ok, now_ok = not was["failures"], not now["failures"]
        if was_ok and not now_ok:
            regressions.append(case_id)
        elif now_ok and not was_ok:
            fixed.append(case_id)
        if was["got"] != now["got"]:
            changed.append({"id": case_id, "before": was["got"], "after": now["got"]})
    old, new = previous["summary"], current["summary"]
    deltas = {
        key: round(new[key] - old[key], 4)
        for key in new
        if key in old and isinstance(new[key], (int, float)) and new[key] != old[key]
    }
    return {
        "regressions": regressions,
        "fixed": fixed,
        "outcome_changed": changed,
        "added": sorted(after.keys() - before.keys()),
        "removed": sorted(before.keys() - after.keys()),
        "deltas": deltas,
        "prompt_version": [previous.get("prompt_version"), current.get("prompt_version")],
    }


# ------------------------------------------------------------------ prompt fingerprint


def prompt_fingerprint() -> str:
    """A hash of the assessment prompt as it is rendered for a fixed sample campaign. Any change
    to its wording, or to how the campaign rules are written into it, changes the hash, so the
    prompt version cannot stay the same after the prompt changed (``tests/test_evals.py``)."""
    sample = CampaignProfile(
        offer="Sample offer",
        countries=["SA"],
        industries=["Software"],
        company_size_min=10,
        company_size_max=500,
        business_model="b2b",
        excluded_industries=["Government"],
        excluded_company_types=[],
        target_departments=["Sales"],
        preferred_buyer_titles=["Head of Sales"],
        outreach_languages=["en"],
        custom_rules="",
    )
    spec = assessment_prompt(sample, "Sample Co", {"industry": "Software"}, [])
    text = "\n---\n".join((spec.name, spec.version, spec.system, spec.user))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_fingerprint() -> dict[str, str]:
    body = {"prompt": "icp_fit", "version": ASSESS_VERSION, "sha256": prompt_fingerprint()}
    FINGERPRINT_FILE.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return body
