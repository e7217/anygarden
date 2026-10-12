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


def test_collects_ids_from_bracketed_test_titles(tmp_path):
    junit = _write_junit(
        tmp_path,
        _case("Inbox &gt; shows approval [INB-04]", None),
        _case("reconnects [MSG-04] [MSG-05]", None, "fail"),
    )

    results = req_report.collect([junit])

    assert [r.outcome for r in results["INB-04"]] == ["passed"]
    assert [r.outcome for r in results["MSG-05"]] == ["failed"]


def _write_spec(tmp_path: Path) -> Path:
    spec = tmp_path / "requirements.md"
    spec.write_text(
        "| ID | 요구 | 검증 |\n|---|---|---|\n"
        "| AUTH-08 | 초대 회수 | pytest |\n"
        "| SYS-04 | 빌드가 통과한다 | build |\n"
        "| USE-06 | 예산 UI | gap |\n"
        "| INB-04 | 인박스 | e2e, vitest |\n",
        encoding="utf-8",
    )
    return spec


def test_spec_layers_reads_the_verification_column(tmp_path):
    layers = req_report.spec_layers(_write_spec(tmp_path))

    assert layers == {
        "AUTH-08": {"pytest"},
        "SYS-04": {"build"},
        "USE-06": {"gap"},
        "INB-04": {"e2e", "vitest"},
    }


def test_require_coverage_passes_when_declared_layers_are_covered(tmp_path, capsys):
    junit = _write_junit(
        tmp_path, _case("test_a", "AUTH-08"), _case("inbox [INB-04]", None)
    )
    spec = _write_spec(tmp_path)

    assert req_report.main([str(junit), "--spec", str(spec), "--require-coverage"]) == 0
    out = capsys.readouterr().out
    assert "| SYS-04 | build | 0 |" in out
    assert "| USE-06 | gap | 0 |" in out
    assert "Known gaps (1): USE-06" in out


def test_require_coverage_fails_on_untested_and_unknown_ids(tmp_path, capsys):
    junit = _write_junit(tmp_path, _case("test_a", "AUTH-99"))
    spec = _write_spec(tmp_path)

    assert req_report.main([str(junit), "--spec", str(spec), "--require-coverage"]) == 1
    out = capsys.readouterr().out
    assert "AUTH-99: tagged in tests but not listed in the spec" in out
    assert "AUTH-08: declared pytest but no passing test" in out
    assert "INB-04: declared e2e/vitest but no passing test" in out
    assert "SYS-04" not in out.split("Coverage problems:")[1]


def test_require_coverage_needs_spec(tmp_path):
    junit = _write_junit(tmp_path, _case("test_a", "AUTH-08"))

    with pytest.raises(SystemExit):
        req_report.main([str(junit), "--require-coverage"])
