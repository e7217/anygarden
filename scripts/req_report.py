#!/usr/bin/env python3
"""Report requirement verification status from pytest JUnit XML (#781).

Tests declare the requirements they verify with ``@pytest.mark.req("AUTH-08")``;
the package conftests record those IDs as a JUnit ``req`` property. This script
reads one or more JUnit files and prints a Markdown table of each requirement's
status. With ``--spec``, requirement IDs listed in the first column of the
spec's Markdown tables are included even when no test covers them.

    uv run pytest packages/cluster --junitxml=/tmp/cluster.xml
    python scripts/req_report.py /tmp/cluster.xml --spec docs/features.md

Exit status is 1 when any requirement has a failing test, else 0.
Standard library only, so it runs without the workspace environment.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_TABLE_ID = re.compile(r"^\|\s*([A-Z]{2,5}-\d{2,3})\s*\|", re.MULTILINE)


@dataclass(frozen=True)
class Result:
    test: str
    outcome: str  # "passed" | "failed" | "error" | "skipped"


def _outcome(case: ET.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return "failed" if tag == "failure" else tag
    return "passed"


def collect(paths: list[Path]) -> dict[str, list[Result]]:
    """Map requirement ID -> results of the tests that carry it."""
    results: dict[str, list[Result]] = defaultdict(list)
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            for prop in case.iter("property"):
                if prop.get("name") != "req":
                    continue
                test = f"{case.get('classname')}::{case.get('name')}"
                for req in filter(None, (prop.get("value") or "").split(",")):
                    results[req.strip()].append(Result(test, _outcome(case)))
    return dict(results)


def status_of(results: list[Result]) -> str:
    """Roll test outcomes up to one requirement status."""
    outcomes = {r.outcome for r in results}
    if not outcomes:
        return "untested"
    if outcomes & {"failed", "error"}:
        return "fail"
    if "passed" in outcomes:
        return "pass"
    return "skipped"


def spec_ids(path: Path) -> list[str]:
    """Requirement IDs from the first column of the spec's Markdown tables."""
    return sorted(set(_TABLE_ID.findall(path.read_text(encoding="utf-8"))))


def build_rows(
    results: dict[str, list[Result]], ids: list[str] | None = None
) -> list[tuple[str, str, int]]:
    """Rows of (requirement ID, status, test count), sorted by ID."""
    all_ids = sorted(set(results) | set(ids or []))
    return [
        (req, status_of(results.get(req, [])), len(results.get(req, [])))
        for req in all_ids
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("junit", nargs="+", type=Path, help="pytest JUnit XML files")
    parser.add_argument("--spec", type=Path, help="Markdown spec listing requirement IDs")
    args = parser.parse_args(argv)

    results = collect(args.junit)
    rows = build_rows(results, spec_ids(args.spec) if args.spec else None)

    print("| ID | Status | Tests |")
    print("|---|---|---|")
    for req, status, count in rows:
        print(f"| {req} | {status} | {count} |")
    failing = [
        f"{req}: {r.test}"
        for req, _, _ in rows
        for r in results.get(req, [])
        if r.outcome in {"failed", "error"}
    ]
    if failing:
        print("\nFailing tests:")
        for line in failing:
            print(f"- {line}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
