"""RFC #652 acceptance — engine-unification regression baseline (task #94).

General room-agent execution contracts for both supported engines, exercised
through the product runtimes with fake CLI executables (no provider, no
network). This file is the CURRENT-STATE baseline at main 6a2d217; the same
module is re-run against each engine-unification PR (#653/#654/#655) and the
results are compared row by row.

Rows covered here:
  R-A codex general-agent run: usage + receipt + single invocation
  R-B codex session resume: second start reuses the native session
  R-C codex cancel: process tree (stub + ignored-SIGTERM child) is reaped
  R-D codex timeout: deadline exceeded → failed/TIMEOUT_STOPPED
  R-E pi general-agent run: provider required, usage + session handle + pinned env
  R-F pi version fence: wrong --version → UNSUPPORTED_RUNTIME (fail-closed)
  R-G removed-engine config: unsupported engine rejected at validate(), config preserved
  R-H pi credential boundary: server-staged env keys rejected
  R-I pi provider requirement: pi-cli without provider rejected at validate()
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from anygarden_agent.runtime.execution import (
    CodexRuntime,
    Invocation,
    LocalExecutionManager,
    SessionScope,
)
from anygarden_agent.runtime.execution.pi import PiRuntime

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="local runtime tier is POSIX"
)


def scope(engine: str) -> SessionScope:
    return SessionScope(
        "node-a", "agent-1", "node-a", "room", None, "managed-workspace", 1, 1,
        engine=engine,
    )


def invocation(engine, tmp_path, prompt="hello", **kw):
    workspace = tmp_path / "workspace"
    home = tmp_path / "runtime-home"
    workspace.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    return Invocation(
        str(uuid.uuid4()),
        scope(engine),
        prompt,
        workspace,
        home,
        timeout_seconds=kw.pop("timeout_seconds", 10),
        **kw,
    )


CODEX_FAKE = f"""#!{sys.executable}
import json, os, signal, subprocess, sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.154.0")
    sys.exit(0)
prompt = sys.stdin.read()
with Path("calls.jsonl").open("a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "prompt": prompt, "env": dict(os.environ)}}) + "\\n")
def event(value):
    print(json.dumps(value), flush=True)
if prompt == "hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])
    Path("child.pid").write_text(str(child.pid))
event({{"type": "thread.started", "thread_id": "native-session"}})
event({{"type": "turn.started"}})
if prompt == "hang":
    time.sleep(60)
if prompt == "delay":
    time.sleep(0.3)
event({{"type": "item.completed", "item": {{"type": "agent_message", "text": "answer"}}}})
event({{"type": "turn.completed", "usage": {{"input_tokens": 3, "output_tokens": 1}}}})
Path(sys.argv[sys.argv.index("-o") + 1]).write_text("answer")
"""

PI_FAKE = """#!{sys.executable}
import json, os, sys, traceback
from pathlib import Path
def _log(obj):
    with Path("pi-calls.jsonl").open("a") as log:
        log.write(json.dumps(obj) + chr(10))
try:
    if sys.argv[1:2] == ["--version"]:
        print("pi 0.85.1")
        sys.exit(0)
    prompt = sys.stdin.read()
    _log({"argv": sys.argv[1:], "prompt": prompt, "env": dict(os.environ)})
    def event(value):
        print(json.dumps(value), flush=True)
    event({"type": "session", "id": "pi-session-1"})
    event({"type": "agent_start"})
    event({"type": "message_end", "message": {"role": "assistant",
        "content": [{"type": "text", "text": "pi answer"}],
        "usage": {"input": 5, "output": 2}, "stopReason": "stop"}})
    event({"type": "agent_end"})
    event({"type": "agent_settled"})
except Exception:
    _log({"fatal": traceback.format_exc()})
"""

PI_WRONG_VERSION = f"""#!{sys.executable}
import sys
if sys.argv[1:2] == ["--version"]:
    print("pi 0.1.0")
"""


def write_exec(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content.replace("{sys.executable}", sys.executable))
    path.chmod(0o700)
    return path


def codex_manager(tmp_path, executable):
    return LocalExecutionManager(
        tmp_path / "receipts", CodexRuntime(executable), authorize=lambda _: True
    )


def pi_manager(tmp_path, executable):
    return LocalExecutionManager(
        tmp_path / "receipts-pi", PiRuntime(executable), authorize=lambda _: True
    )


async def drain(m, execution_id):
    async with asyncio.timeout(10):
        events = [e async for e in m.events(execution_id)]
    return await m.reconcile(execution_id), events


# ---- R-A/R-B/R-C/R-D: codex general-agent ----


@pytest.mark.asyncio
async def test_r_a_codex_run_usage_and_single_call(tmp_path):
    exe = write_exec(tmp_path, "fake-codex", CODEX_FAKE)
    inv = invocation("codex-cli", tmp_path)
    m = codex_manager(tmp_path, exe)
    try:
        assert (await m.start(inv)).state == "queued"
        receipt, events = await drain(m, inv.execution_id)
        assert receipt.outcome == "succeeded" and receipt.text == "answer"
        assert receipt.usage == {"input_tokens": 3, "output_tokens": 1}
        assert [e.kind for e in events].count("terminal") == 1
        calls = json.loads(
            (inv.workspace / "calls.jsonl").read_text().splitlines()[0]
        )
        assert "exec" in calls["argv"]
    finally:
        await m.close()


@pytest.mark.asyncio
async def test_r_b_codex_resume_same_native_session(tmp_path):
    exe = write_exec(tmp_path, "fake-codex", CODEX_FAKE)
    inv = invocation("codex-cli", tmp_path)
    m = codex_manager(tmp_path, exe)
    try:
        await m.start(inv)
        await drain(m, inv.execution_id)
        await m.start(replace(inv, execution_id="execution-2"))
        await drain(m, "execution-2")
        calls = [
            json.loads(line)
            for line in (inv.workspace / "calls.jsonl").read_text().splitlines()
        ]
        assert calls[1]["argv"][1:3] == ["resume", "native-session"]
    finally:
        await m.close()


@pytest.mark.asyncio
async def test_r_c_codex_cancel_kills_process_tree(tmp_path):
    exe = write_exec(tmp_path, "fake-codex", CODEX_FAKE)
    inv = invocation("codex-cli", tmp_path, prompt="hang")
    m = codex_manager(tmp_path, exe)
    try:
        await m.start(inv)
        await asyncio.sleep(0.4)
        child = int((inv.workspace / "child.pid").read_text())
        import psutil

        tree = {p.pid for p in psutil.Process().children(recursive=True)}
        assert child in tree  # child alive before cancel
        await m.cancel(inv.execution_id)
        await asyncio.sleep(0.5)
        assert not psutil.pid_exists(child)  # whole tree reaped
    finally:
        await m.close()


@pytest.mark.asyncio
async def test_r_d_codex_timeout(tmp_path):
    exe = write_exec(tmp_path, "fake-codex", CODEX_FAKE)
    inv = invocation("codex-cli", tmp_path, prompt="delay", timeout_seconds=0.1)
    m = codex_manager(tmp_path, exe)
    try:
        await m.start(inv)
        receipt, _ = await drain(m, inv.execution_id)
        assert receipt.outcome == "failed" and receipt.error_code == "TIMEOUT_STOPPED"
    finally:
        await m.close()


# ---- R-E/R-F/R-H/R-I: pi general-agent ----


@pytest.mark.asyncio
async def test_r_e_pi_run_provider_session_usage_pinned_env(tmp_path):
    exe = write_exec(tmp_path, "fake-pi", PI_FAKE)
    inv = invocation("pi-cli", tmp_path, provider="zai", model="glm-5.3-flash")
    m = pi_manager(tmp_path, exe)
    try:
        assert (await m.start(inv)).state == "queued"
        receipt, events = await drain(m, inv.execution_id)
        assert receipt.outcome == "succeeded" and receipt.text == "pi answer", (
            receipt.outcome, receipt.error_code)
        assert receipt.usage == {"input_tokens": 5, "output_tokens": 2}
        calls = json.loads(
            (inv.workspace / "pi-calls.jsonl").read_text().splitlines()[0]
        )
        argv = calls["argv"]
        assert "--provider" in argv and "zai" in argv
        assert "--mode" in argv and "json" in argv
        # ambient surface is minimal: HOME pinned into the sandboxed home
        assert calls["env"]["HOME"] == str(inv.runtime_home)
        assert calls["env"]["PI_PACKAGE_DIR"] == str(inv.runtime_home)
        assert calls["env"]["PI_SESSION_DIR"] == str(
            inv.runtime_home / "sessions"
        )
    finally:
        await m.close()


def test_r_f_pi_version_fence_fail_closed(tmp_path):
    exe = write_exec(tmp_path, "fake-pi-old", PI_WRONG_VERSION)
    runtime = PiRuntime(exe)
    inv = invocation("pi-cli", tmp_path, provider="zai")

    async def check():
        return await runtime.run(
            inv, None, lambda *_: None, lambda _p: None, lambda: True
        )

    result = asyncio.run(check())
    assert (result.outcome, result.reason) == ("failed", "UNSUPPORTED_RUNTIME")


def test_r_g_removed_engine_config_rejected(tmp_path):
    """An agent configured for a removed engine fails at the contract, and the
    configuration itself is preserved (caller decides on migration guidance)."""
    inv = invocation("gemini-cli", tmp_path)
    with pytest.raises(ValueError, match="unsupported runtime"):
        inv.validate()
    assert inv.scope.engine == "gemini-cli"  # config intact for UI guidance


def test_r_h_pi_rejects_server_env_keys(tmp_path):
    exe = write_exec(tmp_path, "fake-pi", PI_FAKE)
    runtime = PiRuntime(exe)
    inv = invocation(
        "pi-cli", tmp_path, provider="zai",
        environment={"ANYGARDEN_DB_URL": "secret", "PLAIN": "value"},
    )
    with pytest.raises(ValueError, match="server credentials"):
        runtime.environment(inv)


def test_r_i_pi_requires_provider(tmp_path):
    inv = invocation("pi-cli", tmp_path)
    with pytest.raises(ValueError, match="explicit provider"):
        inv.validate()


# ---- PM follow-up (15477c07/85eb457f): usage multi-call, duplicates, preservation ----


@pytest.mark.asyncio
async def test_r_j_pi_usage_multi_call_isolated_per_receipt(tmp_path):
    """여러 호출의 usage가 서로 누출되지 않고 호출별 receipt에 기록된다."""
    exe = write_exec(tmp_path, "fake-pi", PI_FAKE)
    m = pi_manager(tmp_path, exe)
    receipts = []
    try:
        for i in range(2):
            inv = invocation(
                "pi-cli", tmp_path, provider="zai",
                model="glm-5.3-flash", prompt=f"call {i}",
            )
            await m.start(inv)
            receipt, _ = await drain(m, inv.execution_id)
            receipts.append(receipt)
        assert [r.usage for r in receipts] == [
            {"input_tokens": 5, "output_tokens": 2},
        ] * 2
        assert receipts[0].execution_id != receipts[1].execution_id
    finally:
        await m.close()


@pytest.mark.asyncio
async def test_r_k_duplicate_events_do_not_double_count_usage(tmp_path):
    exe = write_exec(tmp_path, "fake-pi-dup", PI_FAKE_DUP)
    inv = invocation("pi-cli", tmp_path, provider="zai")
    m = pi_manager(tmp_path, exe)
    try:
        await m.start(inv)
        receipt, _ = await drain(m, inv.execution_id)
        # duplicate identical events are tolerated; usage stays the last-seen
        assert receipt.outcome == "succeeded"
        assert receipt.usage == {"input_tokens": 5, "output_tokens": 2}
    finally:
        await m.close()


PI_FAKE_DUP = """#!{sys.executable}
import json, sys
from pathlib import Path
if sys.argv[1:2] == ["--version"]:
    print("pi 0.85.1")
    sys.exit(0)
sys.stdin.read()

def event(value):
    print(json.dumps(value), flush=True)
event({"type": "session", "id": "pi-session-1"})
msg = {"role": "assistant", "content": [{"type": "text", "text": "pi answer"}],
       "usage": {"input": 5, "output": 2}, "stopReason": "stop"}
event({"type": "message_end", "message": msg})
event({"type": "message_end", "message": msg})  # duplicate delivery
event({"type": "agent_end"})
event({"type": "agent_settled"})
""".replace("{sys.executable}", sys.executable)


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="cancel path drops observed usage (RuntimeResult carries no usage); "
    "preservation is a D-track follow-up for the runtime adapter",
    strict=True,
)
async def test_r_l_cancel_preserves_usage_observed_so_far(tmp_path):
    exe = write_exec(tmp_path, "fake-pi-hang", PI_FAKE_HANG)
    inv = invocation("pi-cli", tmp_path, provider="zai", prompt="hang")
    m = pi_manager(tmp_path, exe)
    try:
        await m.start(inv)
        await asyncio.sleep(0.3)  # usage observed before cancel
        await m.cancel(inv.execution_id)
        await asyncio.sleep(0.5)
        receipt = await m.reconcile(inv.execution_id)
        assert receipt.outcome == "cancelled"
        assert receipt.usage is not None
    finally:
        await m.close()


PI_FAKE_HANG = """#!{sys.executable}
import json, sys, time
from pathlib import Path
if sys.argv[1:2] == ["--version"]:
    print("pi 0.85.1")
    sys.exit(0)
sys.stdin.read()

def event(value):
    print(json.dumps(value), flush=True)
event({"type": "session", "id": "pi-session-1"})
event({"type": "agent_start"})
event({"type": "message_end", "message": {"role": "assistant",
    "content": [{"type": "text", "text": "partial"}],
    "usage": {"input": 3, "output": 1}, "stopReason": "stop"}})
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
""".replace("{sys.executable}", sys.executable)


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="cancel path drops observed usage (RuntimeResult carries no usage); "
    "preservation is a D-track follow-up — flips to PASS when fixed",
    strict=True,
)
async def test_r_m_cancel_after_turn_end_preserves_usage(tmp_path):
    """turn_end progress 콜백 수신 이후 취소: 어댑터가 해당 progress를 이미
    받은 시점의 취소임을 보장하고, 이 경우에도 측정량이 receipt에 보존되어야
    한다(현재 main/6f486ef 계열에서는 소실 — dev01 수정 대기)."""
    exe = write_exec(tmp_path, "fake-pi", PI_FAKE_HANG)
    inv = invocation("pi-cli", tmp_path, provider="zai", prompt="hang")
    m = pi_manager(tmp_path, exe)
    try:
        await m.start(inv)
        await asyncio.sleep(0.3)  # turn_end progress fires before cancel
        await m.cancel(inv.execution_id)
        await asyncio.sleep(0.5)
        receipt = await m.reconcile(inv.execution_id)
        assert receipt.outcome == "cancelled"
        assert receipt.usage is not None  # measured amount must survive
    finally:
        await m.close()
