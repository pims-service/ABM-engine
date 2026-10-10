"""Fail when a group of modules is below its own coverage floor (issue #53).

``pytest`` already enforces the overall 90% gate (``fail_under`` in pyproject.toml). The issue
also asks for the data model and the permission layer to be above 85% *each*, which a project
total can hide. Run after ``pytest`` (it reads the ``.coverage`` file in the current directory):

    python scripts/check_module_coverage.py            # default 85
    python scripts/check_module_coverage.py --floor 90
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import sys

import coverage

#: Groups checked: every file matching a pattern must reach the floor on its own.
GROUPS: dict[str, list[str]] = {
    "models": ["apps/*/models.py"],
    "permissions": [
        "apps/core/permissions.py",
        "apps/core/tenancy.py",
        "apps/core/roles.py",
    ],
}


def percent(cov: coverage.Coverage, filename: str) -> float:
    return float(cov.report(include=[filename], file=io.StringIO(), skip_empty=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--floor", type=float, default=85.0)
    args = parser.parse_args()

    cov = coverage.Coverage()
    cov.load()
    measured = {name.replace("\\", "/"): name for name in cov.get_data().measured_files()}
    failed = False
    for group, patterns in GROUPS.items():
        matched = sorted(
            real
            for shown, real in measured.items()
            if any(fnmatch.fnmatch(shown, f"*{pattern}") for pattern in patterns)
        )
        if not matched:
            print(f"{group}: no measured files match {patterns}")
            failed = True
            continue
        for name in matched:
            value = percent(cov, name)
            ok = value >= args.floor
            failed |= not ok
            print(f"{'ok  ' if ok else 'FAIL'} {group:<12} {value:6.2f}%  {name}")
    if failed:
        print(f"\nCoverage below {args.floor:g}% for at least one module.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
