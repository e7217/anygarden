"""Machine-side campaign evaluation runner (#930 §3.7, plan CMP-04/05)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from anygarden.machine.campaign_eval import (
    CampaignEvalRunner,
    EvalJob,
    EvalResult,
    compute_pins,
    eval_environment,
    parse_score_line,
    run_eval,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

AGENT = "agent-1"


@pytest.fixture()
def agents_root(tmp_path: Path) -> Path:
    root = tmp_path / "agents"
    (root / AGENT).mkdir(parents=True)
    return root


def _job(command: str, **overrides) -> EvalJob:
    values = {"job_id": "job-1", "agent_id": AGENT, "command": command, "timeout_sec": 10}
    values.update(overrides)
    return EvalJob(**values)


def _py(code: str) -> str:
    return f"{sys.executable} -c {json.dumps(code)}"


@pytest.mark.req("CMP-04")
async def test_success_parses_the_last_json_line(agents_root):
    (agents_root / AGENT / "bench").mkdir()
    command = _py(
        "import json, os; print('warming up'); print(os.getcwd());"
        "print(json.dumps({'score': 0.75, 'valid': True, 'latency': 12, 'note': 'x'}))"
    )
    result = await run_eval(agents_root, _job(command, cwd="bench"))
    assert result.status == "ok", result
    assert result.exit_code == 0
    assert result.parsed == {"score": 0.75, "valid": True, "latency": 12, "note": "x"}
    assert "warming up" in result.stdout_tail
    assert str((agents_root / AGENT / "bench").resolve()) in result.stdout_tail


async def test_custom_score_key(agents_root):
    command = _py("print('{\"acc\": 3, \"valid\": false}')")
    result = await run_eval(agents_root, _job(command, score_key="acc"))
    assert result.status == "ok"
    assert result.parsed == {"acc": 3.0, "valid": False}


@pytest.mark.req("CMP-04")
async def test_timeout_kills_the_whole_process_group(agents_root, tmp_path):
    pid_file = tmp_path / "child.pid"
    # The shell starts a background sleeper (a grandchild), then waits.
    command = f"sleep 60 & echo $! > {pid_file}; wait"
    started = time.monotonic()
    result = await run_eval(agents_root, _job(command, timeout_sec=1))
    assert result.status == "timed_out" and result.error == "timeout"
    assert time.monotonic() - started < 8
    child = int(pid_file.read_text().strip())
    await asyncio.sleep(0.2)
    assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("print('not json')", "last_line_not_json"),
        ("print('[1, 2]')", "last_line_not_object"),
        ("print('{\"valid\": true}')", "score_missing"),
        ("print('{\"score\": true, \"valid\": true}')", "score_missing"),
        ("print('{\"score\": 1.0}')", "valid_missing"),
        ("print('{\"score\": 1.0, \"valid\": \"yes\"}')", "valid_missing"),
        ("pass", "no_output"),
    ],
)
async def test_parse_failures_fail_the_job(agents_root, code, error):
    result = await run_eval(agents_root, _job(_py(code)))
    assert result.status == "failed"
    assert result.error == error


@pytest.mark.req("CMP-04")
async def test_non_zero_exit_fails_even_with_a_score(agents_root):
    command = _py("import sys; print('{\"score\": 1, \"valid\": true}'); sys.exit(3)")
    result = await run_eval(agents_root, _job(command))
    assert (result.status, result.exit_code, result.error) == ("failed", 3, "exit_code")


def test_parse_ignores_trailing_blank_lines_and_bounds_extras():
    parsed, error = parse_score_line(
        'log\n{"score": 2, "valid": true, "big": "' + "x" * 500 + '", "obj": {"a": 1}}\n\n  \n',
        "score",
    )
    assert error is None
    assert parsed["score"] == 2.0 and parsed["valid"] is True
    assert len(parsed["big"]) == 200 and "obj" not in parsed


@pytest.mark.req("CMP-05")
async def test_pin_mismatch_is_invalid_and_does_not_run(agents_root, tmp_path):
    workspace = agents_root / AGENT
    (workspace / "bench.py").write_text("print('v1')")
    (workspace / "data").mkdir()
    (workspace / "data" / "a.csv").write_text("1,2")
    pins = compute_pins(workspace.resolve(), ["bench.py", "data"])
    assert pins["bench.py"] == hashlib.sha256(b"print('v1')").hexdigest()
    marker = tmp_path / "ran"

    (workspace / "data" / "a.csv").write_text("1,3")
    result = await run_eval(
        agents_root, _job(f"touch {marker}", pin_paths=("bench.py", "data"), expected_pins=pins)
    )
    assert (result.status, result.error) == ("invalid", "pin_mismatch")
    assert result.pins["bench.py"] == pins["bench.py"]
    assert result.pins["data"] != pins["data"]
    assert not marker.exists()


async def test_matching_pins_run_and_are_reported(agents_root):
    workspace = agents_root / AGENT
    (workspace / "bench.py").write_text("x")
    pins = compute_pins(workspace.resolve(), ["bench.py"])
    command = _py("print('{\"score\": 1, \"valid\": true}')")
    result = await run_eval(
        agents_root, _job(command, pin_paths=("bench.py",), expected_pins=pins)
    )
    assert result.status == "ok" and result.pins == pins


async def test_missing_pin_fails(agents_root):
    result = await run_eval(agents_root, _job("true", pin_paths=("nope.py",)))
    assert (result.status, result.error) == ("failed", "pin_missing")


@pytest.mark.parametrize("cwd", ["..", "../other", "/tmp", "a/../../x"])
async def test_cwd_escape_is_refused(agents_root, cwd, tmp_path):
    marker = tmp_path / "ran"
    result = await run_eval(agents_root, _job(f"touch {marker}", cwd=cwd))
    assert (result.status, result.error) == ("failed", "path_escape")
    assert not marker.exists()


async def test_symlink_cwd_escape_is_refused(agents_root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, agents_root / AGENT / "link")
    marker = tmp_path / "ran"
    result = await run_eval(agents_root, _job(f"touch {marker}", cwd="link"))
    assert (result.status, result.error) == ("failed", "path_escape")
    assert not marker.exists()


async def test_symlink_pin_escape_is_refused(agents_root, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    os.symlink(secret, agents_root / AGENT / "bench.py")
    result = await run_eval(agents_root, _job("true", pin_paths=("bench.py",)))
    assert (result.status, result.error) == ("failed", "path_escape")
    (agents_root / AGENT / "tree").mkdir()
    os.symlink(secret, agents_root / AGENT / "tree" / "leak")
    result = await run_eval(agents_root, _job("true", pin_paths=("tree",)))
    assert (result.status, result.error) == ("failed", "path_escape")


async def test_symlinked_workspace_or_bad_agent_id_is_refused(tmp_path):
    root = tmp_path / "agents"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", root / AGENT)
    result = await run_eval(root, _job("true"))
    assert result.error == "workspace_missing"
    result = await run_eval(root, _job("true", agent_id="../x"))
    assert result.error == "invalid_agent_id"


async def test_environment_has_no_secrets(agents_root, monkeypatch):
    monkeypatch.setenv("ANYGARDEN_TOKEN", "agt_secret")
    monkeypatch.setenv("ANYGARDEN_AGENT_TOKEN", "mcp_secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    command = _py(
        "import json, os; print(json.dumps({'score': 1, 'valid': True, 'env': ' '.join(sorted(os.environ))}))"
    )
    result = await run_eval(agents_root, _job(command))
    assert result.status == "ok"
    names = set(result.parsed["env"].split())
    assert not names & {"ANYGARDEN_TOKEN", "ANYGARDEN_AGENT_TOKEN", "OPENAI_API_KEY",
                        "ANTHROPIC_API_KEY"}
    assert "PATH" in names and "ANYGARDEN_CAMPAIGN_EVAL" in names


def test_environment_uses_only_the_allowlist_of_a_base():
    env = eval_environment({"PATH": "/x", "HOME": "/h", "AWS_SECRET_ACCESS_KEY": "s",
                            "CODEX_API_KEY": "c"})
    assert env["PATH"] == "/x" and env["HOME"] == "/h"
    assert "AWS_SECRET_ACCESS_KEY" not in env and "CODEX_API_KEY" not in env


async def test_stdout_tails_are_bounded(agents_root):
    command = _py("import sys; sys.stderr.write('e' * 10000); print('o' * 10000);"
                  "print('{\"score\": 1, \"valid\": true}')")
    result = await run_eval(agents_root, _job(command))
    assert result.status == "ok"
    assert len(result.stdout_tail) <= 4096 and len(result.stderr_tail) <= 4096
    assert result.stdout_tail.rstrip().endswith('"valid": true}')


# ── Runner (one job per machine) ───────────────────────────────────


async def test_runner_refuses_a_second_job_while_busy_and_resends_finished(agents_root):
    gate = asyncio.Event()
    calls: list[str] = []

    async def fake_run(root, job, *, base_environment=None):
        calls.append(job.job_id)
        await gate.wait()
        return EvalResult(job_id=job.job_id, agent_id=job.agent_id, status="ok",
                          parsed={"score": 1.0, "valid": True})

    sent: list[dict] = []

    async def send(frame):
        sent.append(frame)

    runner = CampaignEvalRunner(agents_root, runner=fake_run)
    assert await runner.submit(_job("x"), send) == (True, None)
    await asyncio.sleep(0)
    assert await runner.submit(_job("x", job_id="job-2"), send) == (False, "busy")
    assert await runner.submit(_job("x"), send) == (True, "already_running")
    gate.set()
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0.01)
    assert [f["job_id"] for f in sent] == ["job-1"]
    assert sent[0]["type"] == "campaign_eval_result"
    # A finished job's result is re-sent, not re-run.
    assert await runner.submit(_job("x"), send) == (True, "already_finished")
    assert [f["job_id"] for f in sent] == ["job-1", "job-1"]
    assert calls == ["job-1"]
    # The machine is free again.
    gate.clear()
    assert await runner.submit(_job("x", job_id="job-2"), send) == (True, None)
    gate.set()
    await runner.close()


async def test_paths_resolve_inside_the_agents_work_dir(agents_root):
    """#956 — codex-cli runs in ``<agent>/workspace``; the evaluator, its
    cwd and its pins resolve where the agent wrote them."""
    work = agents_root / AGENT / "workspace"
    (work / "goals").mkdir(parents=True)
    (work / "goals" / "eval.py").write_text(
        "import json, os; print(json.dumps({'score': 22, 'valid': True, 'cwd': os.getcwd()}))"
    )
    job = _job(f"{sys.executable} eval.py", cwd="goals", pin_paths=("goals/eval.py",),
               work_dir="workspace")
    result = await run_eval(agents_root, job)
    assert result.status == "ok", result
    assert result.parsed["cwd"] == str((work / "goals").resolve())
    assert list(result.pins) == ["goals/eval.py"]
    escaped = await run_eval(agents_root, _job("true", cwd="..", work_dir="workspace"))
    assert escaped.error == "path_escape"


async def test_a_missing_or_symlinked_work_dir_is_refused(agents_root, tmp_path):
    result = await run_eval(agents_root, _job("true", work_dir="workspace"))
    assert result.error == "workspace_missing"
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", agents_root / AGENT / "workspace")
    result = await run_eval(agents_root, _job("true", work_dir="workspace"))
    assert result.error == "workspace_missing"


# ── Daemon dispatch ─────────────────────────────────────────────────


async def test_daemon_acks_at_once_and_reports_the_result_later(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from anygarden.machine.daemon import MachineDaemon
    from anygarden.machine.protocol.frames import (
        CampaignEvalAckFrame,
        CampaignEvalResultFrame,
    )

    monkeypatch.setenv("ANYGARDEN_TOKEN", "agt_secret")
    daemon = MachineDaemon(
        server_url="wss://localhost:8000/ws/machines/m-1",
        machine_id="m-1",
        machine_token="t",
        agent_dirs_root=tmp_path / "agents",
        workspace_registry_path=tmp_path / "workspaces.json",
        workspace_signing_key_path=tmp_path / "workspace-signing.key",
    )
    (tmp_path / "agents" / AGENT).mkdir(parents=True)
    sent: list[dict] = []
    daemon._ws = AsyncMock()
    daemon._ws.send = AsyncMock(side_effect=lambda data: sent.append(json.loads(data)))

    command = _py("import json, os; print(json.dumps({'score': 0.5, 'valid': True,"
                  " 'leak': os.environ.get('ANYGARDEN_TOKEN', '')}))")
    await daemon._handle({
        "type": "campaign_eval_request", "request_id": "r-1", "job_id": "job-9",
        "agent_id": AGENT, "command": command, "timeout_sec": 30,
    })
    ack = CampaignEvalAckFrame.model_validate(sent[0])
    assert (ack.request_id, ack.job_id, ack.accepted) == ("r-1", "job-9", True)
    for _ in range(200):
        if len(sent) > 1:
            break
        await asyncio.sleep(0.02)
    result = CampaignEvalResultFrame.model_validate(sent[1])
    assert (result.job_id, result.status) == ("job-9", "ok")
    assert result.parsed == {"score": 0.5, "valid": True, "leak": ""}
    await daemon._campaign_evals.close()


@pytest.mark.parametrize(("engine", "work_dir"), [
    ("codex-cli", "workspace"), ("claude-code", "."), (None, "."),
])
def test_daemon_runs_the_evaluator_in_the_engines_work_dir(tmp_path, engine, work_dir):
    """#956 — the machine knows the layout it spawned; no manifest → root."""
    from anygarden.machine.daemon import MachineDaemon
    from anygarden.machine.protocol.frames import (
        CampaignEvalRequestFrame,
        SyncDesiredStateFrame,
    )

    daemon = MachineDaemon(
        server_url="wss://localhost:8000/ws/machines/m-1",
        machine_id="m-1",
        machine_token="t",
        agent_dirs_root=tmp_path / "agents",
        workspace_registry_path=tmp_path / "workspaces.json",
        workspace_signing_key_path=tmp_path / "workspace-signing.key",
    )
    if engine is not None:
        daemon._manifest_store.save(SyncDesiredStateFrame(
            agent_id=AGENT, desired_state="running", generation=1, engine=engine,
        ))
    job = daemon._eval_job(CampaignEvalRequestFrame(
        request_id="r-1", job_id="job-1", agent_id=AGENT, command="true", timeout_sec=5,
    ))
    assert job.work_dir == work_dir
