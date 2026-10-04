"""Tests for ``scripts/req_report.py`` — requirement status from JUnit XML (#781)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "req_report.py"
_spec = importlib.util.spec_from_file_location("req_report", _SCRIPT)
req_report = importlib.util.module_from_spec(_spec)
# dataclasses resolve their module through sys.modules.
sys.modules.setdefault("req_report", req_report)
_spec.loader.exec_module(req_report)


def _case(name: str, req: str | None, outcome: str = "pass") -> str:
    props = (
        f'<properties><property name="req" value="{req}"/></properties>'
        if req
        else ""
    )
    body = {
        "pass": "",
        "fail": '<failure message="boom">trace</failure>',
        "error": '<error message="boom">trace</error>',
        "skip": '<skipped message="later"/>',
    }[outcome]
    return f'<testcase classname="tests.test_x" name="{name}">{props}{body}</testcase>'


def _write_junit(tmp_path: Path, *cases: str) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite name="pytest">'
        + "".join(cases)
        + "</testsuite></testsuites>"
    )
    return path


def test_collects_outcomes_per_requirement(tmp_path):
    junit = _write_junit(
        tmp_path,
        _case("test_a", "AUTH-08"),
        _case("test_b", "AUTH-08,ROOM-01", "fail"),
        _case("test_c", None),
    )

    results = req_report.collect([junit])

    assert {r.outcome for r in results["AUTH-08"]} == {"passed", "failed"}
    assert [r.test for r in results["ROOM-01"]] == ["tests.test_x::test_b"]
    assert "test_c" not in str(results)


@pytest.mark.parametrize(
    ("outcomes", "status"),
    [
        (["passed", "passed"], "pass"),
        (["passed", "failed"], "fail"),
        (["passed", "error"], "fail"),
        (["passed", "skipped"], "pass"),
        (["skipped"], "skipped"),
        ([], "untested"),
    ],
)
def test_status_rolls_up_outcomes(outcomes, status):
    results = [req_report.Result("t", o) for o in outcomes]
    assert req_report.status_of(results) == status


def test_spec_ids_without_tests_are_reported_untested(tmp_path):
    junit = _write_junit(tmp_path, _case("test_a", "AUTH-08"))
    spec = tmp_path / "features.md"
    spec.write_text(
        "| ID | 요구 |\n|---|---|\n"
        "| AUTH-08 | 초대 회수 |\n"
        "| ROOM-01 | 프로젝트 권한 |\n"
        "본문에서 AUTH-99를 언급해도 표의 ID가 아니면 무시한다.\n"
    )

    rows = req_report.build_rows(req_report.collect([junit]), req_report.spec_ids(spec))

    assert [(r[0], r[1]) for r in rows] == [("AUTH-08", "pass"), ("ROOM-01", "untested")]


def test_main_exits_nonzero_when_a_requirement_fails(tmp_path, capsys):
    junit = _write_junit(tmp_path, _case("test_a", "AUTH-08", "fail"))

    assert req_report.main([str(junit)]) == 1
    assert "AUTH-08" in capsys.readouterr().out


def test_main_exits_zero_when_all_pass(tmp_path):
    junit = _write_junit(tmp_path, _case("test_a", "AUTH-08"))

    assert req_report.main([str(junit)]) == 0
