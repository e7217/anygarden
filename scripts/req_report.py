#!/usr/bin/env python3
"""Report requirement verification status from JUnit XML (#781).

Tests declare the requirements they verify:

- pytest: ``@pytest.mark.req("AUTH-08")``; the package conftests record the
  IDs as a JUnit ``req`` property.
- vitest / Playwright: the ID in square brackets in the test title, e.g.
  ``it('reconnects after a drop [MSG-04]')``.

This script reads one or more JUnit files and prints a Markdown table of each
requirement's status. With ``--spec``, requirement IDs listed in the first
column of the spec's Markdown tables are included even when no test covers
them. When the spec table has a ``검증`` column, each row declares how the
requirement is verified: ``pytest``, ``vitest``, ``e2e``, ``build`` (the
frontend build/typecheck) or ``gap`` (known unimplemented).

    uv run pytest packages/cluster --junitxml=/tmp/cluster.xml
    python scripts/req_report.py /tmp/cluster.xml --spec docs/requirements.md

Exit status is 1 when any requirement has a failing test. With
``--require-coverage`` it is also 1 when a requirement declared as tested has
no passing test, or a test names an ID the spec does not list.
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

_ID = r"[A-Z]{2,5}-\d{2,3}"
_TABLE_ID = re.compile(rf"^\|\s*({_ID})\s*\|", re.MULTILINE)
_TITLE_ID = re.compile(rf"\[({_ID})\]")
_ROW = re.compile(rf"^\|\s*({_ID})\s*\|(.*)\|\s*$")

TEST_LAYERS = frozenset({"pytest", "vitest", "e2e"})
LAYERS = TEST_LAYERS | {"build", "gap"}


@dataclass(frozen=True)
class Result:
    test: str
    outcome: str  # "passed" | "failed" | "error" | "skipped"


def _outcome(case: ET.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return "failed" if tag == "failure" else tag
    return "passed"


def _case_ids(case: ET.Element) -> set[str]:
    ids = set(_TITLE_ID.findall(case.get("name") or ""))
    for prop in case.iter("property"):
        if prop.get("name") == "req":
            ids.update(i.strip() for i in (prop.get("value") or "").split(",") if i.strip())
    return ids


def collect(paths: list[Path]) -> dict[str, list[Result]]:
    """Map requirement ID -> results of the tests that carry it."""
    results: dict[str, list[Result]] = defaultdict(list)
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            test = f"{case.get('classname')}::{case.get('name')}"
            for req in sorted(_case_ids(case)):
                results[req].append(Result(test, _outcome(case)))
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


def spec_layers(path: Path) -> dict[str, set[str]]:
    """Requirement ID -> declared verification layers (empty if undeclared).

    Reads the last column of each requirement row and keeps the words that
    name a layer, so a spec without a ``검증`` column yields empty sets.
    """
    layers: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if not match:
            continue
        last = match.group(2).rsplit("|", 1)[-1]
        words = {w.strip().lower() for w in last.split(",")}
        layers[match.group(1)] = words & LAYERS
    return layers


def build_rows(
    results: dict[str, list[Result]], ids: list[str] | None = None
) -> list[tuple[str, str, int]]:
    """Rows of (requirement ID, status, test count), sorted by ID."""
    all_ids = sorted(set(results) | set(ids or []))
    return [
        (req, status_of(results.get(req, [])), len(results.get(req, [])))
        for req in all_ids
    ]


def declared_status(status: str, layers: set[str]) -> str:
    """Refine a test-derived status with what the spec declares."""
    if status != "untested":
        return status
    if "gap" in layers:
        return "gap"
    if layers and not layers & TEST_LAYERS:
        return "build" if "build" in layers else status
    return status


def coverage_problems(
    results: dict[str, list[Result]], layers: dict[str, set[str]]
) -> list[str]:
    """Reasons the suite does not cover the spec, one line each."""
    problems = []
    for req in sorted(set(results) - set(layers)):
        problems.append(f"{req}: tagged in tests but not listed in the spec")
    for req, declared in sorted(layers.items()):
        status = status_of(results.get(req, []))
        if not declared:
            problems.append(f"{req}: no verification layer declared")
        elif declared & TEST_LAYERS and status in {"untested", "skipped"}:
            problems.append(f"{req}: declared {'/'.join(sorted(declared))} but no passing test")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("junit", nargs="+", type=Path, help="JUnit XML files")
    parser.add_argument("--spec", type=Path, help="Markdown spec listing requirement IDs")
    parser.add_argument(
        "--require-coverage",
        action="store_true",
        help="fail when a requirement declared as tested has no passing test",
    )
    args = parser.parse_args(argv)
    if args.require_coverage and not args.spec:
        parser.error("--require-coverage needs --spec")

    results = collect(args.junit)
    layers = spec_layers(args.spec) if args.spec else {}
    rows = build_rows(results, spec_ids(args.spec) if args.spec else None)

    print("| ID | Status | Tests |")
    print("|---|---|---|")
    for req, status, count in rows:
        print(f"| {req} | {declared_status(status, layers.get(req, set()))} | {count} |")
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
    gaps = sorted(req for req, declared in layers.items() if "gap" in declared)
    if gaps:
        print(f"\nKnown gaps ({len(gaps)}): {', '.join(gaps)}")
    problems = coverage_problems(results, layers) if args.require_coverage else []
    if problems:
        print("\nCoverage problems:")
        for line in problems:
            print(f"- {line}")
    return 1 if failing or problems else 0


if __name__ == "__main__":
    sys.exit(main())
