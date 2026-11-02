"""Score the qualification step against the labelled evaluation set (issues #120 and #183).

    python manage.py run_evals                       # recorded answers, offline
    python manage.py run_evals --out run.json        # also write the report
    python manage.py run_evals --against old.json    # and say what changed since that report
    python manage.py run_evals --live --client <id>  # the client's AI connection answers
    python manage.py run_evals --live --client <id> --record   # and store those answers
    python manage.py run_evals --write-fingerprint   # after a prompt change, with a version bump

Exit status 1 when the pass rate is below ``--fail-under`` (default 1.0 for recorded answers, so a
regression in the hard rules, the schema or the trigger windows fails the run) or when a case
that passed before in ``--against`` fails now. See docs/research/evaluation.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.research.evals import harness


class Command(BaseCommand):
    help = "Score the qualification step against the labelled evaluation set."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--live", action="store_true", help="Ask the client's AI connection.")
        parser.add_argument("--client", help="Client id whose AI connection --live uses.")
        parser.add_argument(
            "--record", action="store_true", help="With --live: store the answers in the cases."
        )
        parser.add_argument("--case", action="append", default=[], help="Only this case id.")
        parser.add_argument("--out", help="Write the report as JSON to this path.")
        parser.add_argument("--against", help="A report from an earlier run to compare with.")
        parser.add_argument(
            "--fail-under",
            type=float,
            default=None,
            help="Lowest accepted pass rate (default 1.0 recorded, 0.0 live).",
        )
        parser.add_argument(
            "--write-fingerprint",
            action="store_true",
            help="Rewrite prompt_fingerprint.json for the current prompt.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["write_fingerprint"]:
            body = harness.write_fingerprint()
            self.stdout.write(f"fingerprint written: version {body['version']} {body['sha256']}")
            return
        cases = harness.load_cases(only=options["case"])
        if not cases:
            raise CommandError("No cases found.")
        if options["live"]:
            report = self._live(cases, options)
        else:
            if options["record"]:
                raise CommandError("--record needs --live.")
            report = harness.run_recorded(cases)

        self._print(report)
        payload = report.to_json()
        if options["out"]:
            Path(options["out"]).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        regressed = False
        if options["against"]:
            previous = json.loads(Path(options["against"]).read_text(encoding="utf-8"))
            diff = harness.compare(previous, payload)
            self._print_diff(diff)
            regressed = bool(diff["regressions"])
        threshold = options["fail_under"]
        if threshold is None:
            threshold = 0.0 if options["live"] else 1.0
        if report.summary()["pass_rate"] < threshold or regressed:
            raise CommandError("The evaluation did not meet its threshold.")

    def _live(self, cases: list[dict[str, Any]], options: dict[str, Any]) -> harness.Report:
        if not options["client"]:
            raise CommandError("--live needs --client <id>.")
        from apps.integrations.llm_connection import LLMUnavailable, get_llm_adapter

        try:
            adapter = get_llm_adapter(options["client"])
        except LLMUnavailable as exc:
            raise CommandError(str(exc)) from None
        recorded: dict[str, tuple[dict[str, Any], str]] = {}

        def keep(case: dict[str, Any], output: dict[str, Any], model: str) -> None:
            recorded[case["id"]] = (output, model)

        report = harness.run_live(cases, adapter, keep if options["record"] else None)
        if options["record"]:
            self._record(cases, recorded)
        return report

    def _record(
        self, cases: list[dict[str, Any]], recorded: dict[str, tuple[dict[str, Any], str]]
    ) -> None:
        from datetime import date

        for path in sorted(harness.CASES_DIR.glob("*.json")):
            case = json.loads(path.read_text(encoding="utf-8"))
            if case["id"] not in recorded:
                continue
            output, model = recorded[case["id"]]
            case["model_output"] = output
            case["model_output_source"] = f"recorded:{model}@{date.today().isoformat()}"
            path.write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.stdout.write(f"recorded {len(recorded)} answers")

    def _print(self, report: harness.Report) -> None:
        summary = report.summary()
        self.stdout.write(
            f"{report.mode} run, prompt v{report.prompt_version}, model {report.model}: "
            f"{summary['passed']}/{summary['cases']} cases pass "
            f"(fit {summary['fit_correct']}, recommendation {summary['recommendation_correct']}, "
            f"trigger {summary['trigger_correct']})"
        )
        classes = ", ".join(
            f"{name} {entry['correct']}/{entry['cases']}"
            for name, entry in sorted(summary["fit_by_class"].items())
        )
        self.stdout.write(f"  ICP fit by expected class: {classes}")
        for case in report.cases:
            if not case.passed:
                self.stdout.write(f"  FAIL {case.id}: " + "; ".join(case.failures))

    def _print_diff(self, diff: dict[str, Any]) -> None:
        self.stdout.write(
            f"against the earlier report: {len(diff['regressions'])} regressions, "
            f"{len(diff['fixed'])} fixed, {len(diff['outcome_changed'])} outcomes changed, "
            f"{len(diff['added'])} added, {len(diff['removed'])} removed"
        )
        for case_id in diff["regressions"]:
            self.stdout.write(f"  REGRESSION {case_id}")
        for change in diff["outcome_changed"]:
            self.stdout.write(f"  changed {change['id']}: {change['before']} -> {change['after']}")
