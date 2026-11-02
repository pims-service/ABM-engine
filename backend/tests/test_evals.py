"""The labelled evaluation set and its harness (#120, #183). Fully offline: the recorded answers
are the model, and the live mode is driven by a scripted fake."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.integrations.framework.fakes import FakeLLMAdapter
from apps.research.engine.prompts import ASSESS_VERSION
from apps.research.evals import harness

pytestmark = [pytest.mark.unit]


class ScriptedLLM(FakeLLMAdapter):
    """Answers each case from ``answers`` by company name, in the shape the harness asks for."""

    def __init__(self, answers: dict[str, Any]) -> None:
        super().__init__({"model": "fake-model"})
        self.answers = answers

    def generate_structured(self, prompt: Any, schema: Any, **kwargs: Any) -> Any:
        name = next(n for n in self.answers if f"Company: {n}\n" in prompt.user)
        self.output = self.answers[name]
        return super().generate_structured(prompt, schema, **kwargs)


# ------------------------------------------------------------------ the cases


def test_there_are_cases_and_each_is_complete() -> None:
    cases = harness.load_cases()
    assert len(cases) >= 12
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert {"id", "campaign", "company", "as_of", "model_output", "expected"} <= set(case)
        assert set(case["expected"]) == {"fit", "recommendation", "trigger"}
        assert case["model_output_source"]


def test_the_set_covers_what_the_brief_insists_on() -> None:
    tags = {tag for case in harness.load_cases() for tag in case.get("tags", [])}
    assert {
        "strong_icp_no_trigger",  # a strong fit needs no trigger (section 2)
        "strong_icp_with_trigger",
        "exclusion",
        "business_model",
        "size",
        "freshness",
        "injection",
        "arabic",
        "negative",
    } <= tags


def test_a_strong_fit_without_a_trigger_is_an_add_in_the_set() -> None:
    cases = [c for c in harness.load_cases() if "strong_icp_no_trigger" in c.get("tags", [])]
    assert len(cases) >= 3
    for case in cases:
        assert case["expected"] == {"fit": "strong", "recommendation": "add", "trigger": False}


# ------------------------------------------------------------------ recorded mode


def test_every_recorded_case_passes() -> None:
    """The guard: the schema, the hard rules and the trigger windows still give the labelled
    outcomes. When this fails, either a rule changed on purpose (update the labels) or broke."""
    report = harness.run_recorded(harness.load_cases())
    failed = {c.id: c.failures for c in report.cases if not c.passed}
    assert failed == {}
    assert report.summary()["pass_rate"] == 1.0


def test_a_wrong_label_is_reported_by_field() -> None:
    case = harness.load_cases(only=["strong-no-trigger-saudi-fintech"])[0]
    case["expected"] = {"fit": "weak", "recommendation": "skip", "trigger": True}
    result = harness.evaluate_case(case, case["model_output"])
    assert [f.split(":")[0] for f in result.failures] == ["fit", "recommendation", "trigger"]


def test_an_answer_that_does_not_fit_the_schema_fails_the_case() -> None:
    case = harness.load_cases(only=["strong-no-trigger-saudi-fintech"])[0]
    bad = {**case["model_output"], "fit": "excellent", "reasons": []}
    result = harness.evaluate_case(case, bad)
    assert any("schema" in f for f in result.failures)
    assert result.got["fit"] is None
    assert harness.evaluate_case(case, "not an object").failures


def test_the_hard_rules_correct_a_model_that_ignores_an_exclusion() -> None:
    case = harness.load_cases(only=["excluded-industry-government"])[0]
    assert case["model_output"]["recommendation"] == "add"  # the model was wrong
    result = harness.evaluate_case(case, case["model_output"])
    assert (result.got["fit"], result.got["recommendation"]) == ("weak", "skip")


def test_the_trigger_does_not_change_the_fit() -> None:
    case = harness.load_cases(only=["strong-with-fresh-funding"])[0]
    with_signal = harness.evaluate_case(case, case["model_output"])
    without = harness.evaluate_case({**case, "signals": []}, case["model_output"])
    assert with_signal.got["trigger"] is True
    assert without.got["trigger"] is False
    assert (with_signal.got["fit"], with_signal.got["recommendation"]) == (
        without.got["fit"],
        without.got["recommendation"],
    )


def test_a_campaign_window_flips_only_the_trigger() -> None:
    inside = harness.load_cases(only=["funding-inside-default-window"])[0]
    beyond = harness.load_cases(only=["funding-beyond-campaign-window"])[0]
    assert harness.trigger_yes(inside) is True
    assert harness.trigger_yes(beyond) is False


# ------------------------------------------------------------------ live mode (scripted)


def live_answers(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {c["company"]["name"]: c["model_output"] for c in cases}


def test_the_live_mode_scores_the_models_answers() -> None:
    cases = harness.load_cases(only=["strong-no-trigger-saudi-fintech", "weak-fit-is-never-an-add"])
    report = harness.run_live(cases, ScriptedLLM(live_answers(cases)))
    assert report.mode == "live"
    assert report.model == "fake-model"
    assert report.summary()["pass_rate"] == 1.0


def test_a_live_model_that_disagrees_lowers_the_score_and_says_where() -> None:
    cases = harness.load_cases(only=["strong-no-trigger-saudi-fintech"])
    wrong = {**cases[0]["model_output"], "fit": "weak", "recommendation": "skip"}
    report = harness.run_live(cases, ScriptedLLM({cases[0]["company"]["name"]: wrong}))
    assert report.summary()["pass_rate"] == 0.0
    assert report.cases[0].failures[0].startswith("fit:")


def test_a_failing_live_call_fails_that_case_not_the_run() -> None:
    cases = harness.load_cases(only=["strong-no-trigger-saudi-fintech", "weak-fit-is-never-an-add"])

    class Flaky(ScriptedLLM):
        def generate_structured(self, prompt: Any, schema: Any, **kwargs: Any) -> Any:
            if "Gulf Pay" in prompt.user:
                raise RuntimeError("boom")
            return super().generate_structured(prompt, schema, **kwargs)

    report = harness.run_live(cases, Flaky(live_answers(cases)))
    outcomes = {c.id: c.passed for c in report.cases}
    assert outcomes == {"strong-no-trigger-saudi-fintech": False, "weak-fit-is-never-an-add": True}
    assert "the model call failed" in report.cases[0].failures[0]


# ------------------------------------------------------------------ comparing runs


def test_compare_names_regressions_fixes_and_changed_outcomes() -> None:
    cases = harness.load_cases(only=["strong-no-trigger-saudi-fintech", "weak-fit-is-never-an-add"])
    before = harness.run_recorded(cases).to_json()
    broken = json.loads(json.dumps(before))
    broken["cases"][0]["failures"] = ["fit: expected 'strong', got 'weak'"]
    broken["cases"][0]["got"]["fit"] = "weak"
    broken["summary"]["passed"] = 1
    diff = harness.compare(before, broken)
    assert diff["regressions"] == [before["cases"][0]["id"]]
    assert diff["outcome_changed"][0]["after"]["fit"] == "weak"
    assert diff["deltas"]["passed"] == -1
    reverse = harness.compare(broken, before)
    assert reverse["fixed"] == [before["cases"][0]["id"]]
    assert reverse["regressions"] == []


def test_compare_notes_added_and_removed_cases() -> None:
    one = harness.run_recorded(
        harness.load_cases(only=["strong-no-trigger-saudi-fintech"])
    ).to_json()
    two = harness.run_recorded(harness.load_cases(only=["weak-fit-is-never-an-add"])).to_json()
    diff = harness.compare(one, two)
    assert diff["added"] == ["weak-fit-is-never-an-add"]
    assert diff["removed"] == ["strong-no-trigger-saudi-fintech"]


# ------------------------------------------------------------------ the command


def run(*args: str) -> str:
    out = io.StringIO()
    call_command("run_evals", *args, stdout=out)
    return out.getvalue()


def test_the_command_scores_writes_a_report_and_compares(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    text = run("--out", str(first))
    assert "recorded run" in text
    assert json.loads(first.read_text(encoding="utf-8"))["summary"]["pass_rate"] == 1.0
    compared = run("--against", str(first))
    assert "0 regressions" in compared


def test_the_command_fails_below_the_threshold_and_on_bad_input() -> None:
    with pytest.raises(CommandError):
        run("--fail-under", "1.1")  # the pass rate cannot reach it
    with pytest.raises(CommandError):
        run("--case", "nonexistent-case")
    with pytest.raises(CommandError):
        run("--record")  # only with --live
    with pytest.raises(CommandError):
        run("--live")  # needs a client


# ------------------------------------------------------------------ prompt versioning


def test_the_prompt_cannot_change_without_a_new_version() -> None:
    """Any change to the prompt wording (or to how the campaign rules are written into it) changes
    the fingerprint. Change ``ASSESS_VERSION`` and run
    ``python manage.py run_evals --write-fingerprint`` together, then re-run the live evaluation
    and commit the results (docs/research/evaluation.md)."""
    stored = json.loads(harness.FINGERPRINT_FILE.read_text(encoding="utf-8"))
    assert stored["version"] == ASSESS_VERSION, "the version changed: rewrite the fingerprint"
    assert stored["sha256"] == harness.prompt_fingerprint(), (
        "the prompt changed without a new version: bump ASSESS_VERSION and rewrite the fingerprint"
    )
