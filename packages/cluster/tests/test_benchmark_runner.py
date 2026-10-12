"""The live benchmark runner (``benchmarks/runner``) stays runnable.

The live runs need a real engine, so they cannot run in CI. What can drift
silently is everything around them: the suite referencing scenarios or
requirement IDs that no longer exist, the seeding calls breaking when the
API changes (the 2026-10 drivers still called the speaker-strategy API
removed in #802), and the ledger arithmetic. Those are checked here
against the in-process app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest
from httpx import ASGITransport

from .test_agents_api import agents_env as agents_env

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from benchmarks.runner import ledger
from benchmarks.runner.__main__ import (
    REQUIREMENTS,
    SUITE,
    load_scenarios,
    validate,
)
from benchmarks.runner.client import Api, Gates
from benchmarks.runner.receiver import MockReceiver
from benchmarks.runner.topology import EngineConfig, seed_solo, seed_team


def test_committed_suite_is_valid():
    assert validate(SUITE, REQUIREMENTS) == []


def test_suite_measures_both_topologies():
    _, scenarios = load_scenarios(SUITE)
    both = [s.id for s in scenarios if {"solo", "team"} <= set(s.modes)]
    assert both, "team-vs-solo uplift needs at least one scenario in both modes"


def test_validate_flags_unknown_requirements_and_holdout_duplicates(tmp_path):
    holdout = tmp_path / "holdout"
    holdout.mkdir()
    spec = {"id": "X", "title": "t", "request": "r", "must_include": ["a"], "requirements": ["NOPE-01"]}
    (holdout / "a.json").write_text(json.dumps(spec))
    (holdout / "b.json").write_text(json.dumps(spec))

    problems = validate(SUITE, REQUIREMENTS, holdout)

    assert "HO-X: requirement NOPE-01 is not in requirements.md" in problems
    assert "HO-X: duplicate scenario id" in problems


def test_gates_need_every_expected_hard_gate():
    g = Gates(expected=2)
    g.check("G1", True)
    g.info("note", "x")
    assert not g.passed  # stopped early: G2 never ran
    g.check("G2", True)
    assert g.passed
    g.check("G3", False)
    assert not g.passed


def _run(scenario, mode, n, status, claimed=None, cost=0.0, judged=None):
    run = {
        "scenario": scenario, "mode": mode, "n": n, "status": status,
        "claimed_done": claimed, "wall_seconds": 60,
        "metrics": {"cost": {"cost_usd": cost, "prompt_tokens": 10, "completion_tokens": 5}},
    }
    if judged:
        run["judged"] = {"status": judged, "reason": "r"}
    return run


def test_rollup_pass_k_false_done_and_cost():
    result = {
        "k": 3,
        "runs": [
            _run("DL-01", "team", 1, "pass", True, 0.3),
            _run("DL-01", "team", 2, "fail", True, 0.3),  # done but wrong: false done
            _run("DL-01", "team", 3, "pass", True, 0.3),
            _run("DL-01", "solo", 1, "pass", True, 0.1),
            _run("DL-01", "solo", 2, "fail", False, 0.1),
            _run("DL-01", "solo", 3, "error"),
            _run("GT-03", "solo", 1, "fail", True, judged="environment"),
        ],
    }

    rows = {(r.scenario, r.mode): r for r in ledger.rollup(result)}

    team = rows[("DL-01", "team")]
    assert (team.verdict, team.passed, team.counted) == ("fail", 2, 3)
    assert team.false_done_rate == pytest.approx(1 / 3)
    assert team.cost_per_pass == pytest.approx(0.45)
    solo = rows[("DL-01", "solo")]
    assert (solo.verdict, solo.counted, solo.excluded) == ("incomplete", 2, 1)
    assert solo.false_done_rate == 0
    assert rows[("GT-03", "solo")].counted == 0

    up = ledger.uplift(list(rows.values()))
    assert up[0]["scenario"] == "DL-01"
    assert up[0]["uplift"] == pytest.approx(2 / 3 - 1 / 2)
    assert up[0]["cost_ratio"] == pytest.approx(3.0)


def test_report_and_annotate_round_trip(tmp_path):
    path = tmp_path / "20261012-0000-abc1234.json"
    result = ledger.new_result({"commit": "abc1234def", "started_at": "2026-10-12T00:00:00", "k": 1})
    result["runs"] = [_run("GT-03", "solo", 1, "fail", True)]
    path.write_text(json.dumps(result))

    assert "| GT-03 | solo | fail | 0/1 |" in ledger.report(ledger.load(tmp_path))
    ledger.annotate(path, "GT-03", "solo", 1, "environment", "sandbox blocks file writes")
    out = ledger.report(ledger.load(tmp_path))
    assert "| GT-03 | solo | incomplete | 0/0 | 1 |" in out
    with pytest.raises(ValueError):
        ledger.annotate(path, "GT-03", "solo", 1, "maybe", "x")


def test_mock_receiver_counts_attempts_and_validates_payload():
    receiver = MockReceiver(port=0).start()
    try:
        content = b"release notes"
        body = json.dumps({
            "approval_id": "ap-1",
            "artifact": {
                "filename": "notes.md",
                "content_base64": base64.b64encode(content).decode(),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        }).encode()

        def post(data: bytes, key: str) -> int:
            req = urllib.request.Request(
                receiver.url, data=data, method="POST",
                headers={"Idempotency-Key": key, "Content-Type": "application/json"},
            )
            try:
                return urllib.request.urlopen(req, timeout=5).status
            except HTTPError as exc:
                return exc.code

        assert post(body, "ap-1") == 201
        assert post(body, "ap-1") == 200  # duplicate delivery is idempotent
        assert post(b"{}", "ap-2") == 400
        assert len(receiver.attempts_for({"ap-1"})) == 2
        assert len(receiver.attempts_for({"ap-2"})) == 1
        assert [r["text"] for r in receiver.receipts_for("ap-1")] == ["release notes"]
    finally:
        receiver.stop()


async def test_seeding_works_against_the_current_api(agents_env):
    transport = ASGITransport(app=agents_env["app"])
    api = Api("http://test", agents_env["token"], transport)
    engine = EngineConfig(engine="echo")
    try:
        team = await seed_team(api, engine, "t", start=False)
        solo = await seed_solo(api, engine, "s", start=False)

        ops = await api(f"/api/v1/rooms/{team.entry}")
        assert ops["representative_agent_id"] == team.agents["lead"]["id"]
        for key in ("dev", "release"):
            room = await api(f"/api/v1/rooms/{team.rooms[key]['id']}")
            assert room["parent_room_id"] == team.entry
            assert room["representative_agent_id"] == team.agents[key]["id"]
        assert set(team.participants) == {"lead"}
        assert set(solo.participants) == {"solo"}

        metrics = await api(
            "/api/v1/metrics/collaboration", params={"project_id": team.project["id"]}
        )
        assert metrics["room_count"] == 3
        assert metrics["turns"]["total"] == 0
    finally:
        await api.close()


class _FakeApi:
    """Answers the three reads a solo deliverable check makes."""

    def __init__(self, tasks, messages, turns):
        self.data = {"tasks": tasks, "messages": messages, "turns": turns}

    async def __call__(self, path, method="GET", body=None, params=None):
        for key in self.data:
            if path.endswith(key):
                return self.data[key]
        raise AssertionError(path)


def _solo_fixture():
    from benchmarks.runner.topology import Fixture

    return Fixture(
        "solo", {"id": "p"}, {"entry": {"id": "room"}}, {"solo": {"id": "agent"}},
        participants={"solo": "pid"},
    )


@pytest.mark.parametrize(
    ("tasks", "turn_state", "expected"),
    [
        ([], "completed", True),  # answered directly, no task needed (#806)
        ([], "leased", False),  # still working
        ([{"source_message_id": "t", "status": "in_progress"}], "completed", False),
        ([{"source_message_id": "t", "status": "done", "result_markdown": "R"}], "completed", True),
    ],
)
async def test_solo_deliverable_accepts_a_direct_answer_or_a_done_task(tasks, turn_state, expected):
    from benchmarks.runner.scenarios import _deliverable_text

    api = _FakeApi(
        tasks,
        [{"participant_id": "pid", "seq": 2, "content": "notes v2.4"}],
        [{"state": turn_state}],
    )

    claimed, text = await _deliverable_text(api, _solo_fixture(), {"id": "t", "seq": 1})

    assert claimed is expected
    assert ("notes v2.4" in text) is expected
