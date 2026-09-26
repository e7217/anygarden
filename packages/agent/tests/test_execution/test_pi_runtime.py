"""Exercise the PiRuntime adapter without a provider (task #64).

The fake pi executable never touches the network: it replays the JSON event
stream shapes observed from pi 0.85.1. These regressions pin the offline
discipline required after the 2026-09-17 incident: no ambient environment
inheritance, no argv prompt/keys, PI_* directories pinned into runtime_home.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace

import psutil
import pytest

from anygarden_agent.runtime.execution import (
    Invocation,
    LocalExecutionManager,
    SessionScope,
)
from anygarden_agent.runtime.execution.pi import PiRuntime

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="first local backend is POSIX"
)


@pytest.fixture
def invocation(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "runtime-home"
    workspace.mkdir()
    home.mkdir()
    return Invocation(
        "execution-1",
        SessionScope(
            "node-a",
            "agent-1",
            "node-a",
            "room",
            None,
            "managed-workspace",
            1,
            1,
            engine="pi-cli",
            engine_version="0.85.1",
        ),
        "success",
        workspace,
        home,
        provider="zai",
        model="glm-5.3-flash",
        timeout_seconds=2,
    )


@pytest.fixture
def executable(tmp_path):
    path = tmp_path / "fake-pi"
    path.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, signal, subprocess, sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("pi " + os.environ.get("FAKE_VERSION", "0.85.1"))
    sys.exit(0)
prompt = sys.stdin.read()
with Path("calls.jsonl").open("a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "env": dict(os.environ)}) + "\n")
def event(value):
    print(json.dumps(value), flush=True)
event({"type": "session", "version": 3, "id": "native-pi-session"})
event({"type": "agent_start"})
event({"type": "turn_start"})
event({"type": "message_start", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
event({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
if prompt == "hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])
    Path("child.pid").write_text(str(child.pid))
if prompt == "delay":
    time.sleep(0.15)
if prompt == "failed":
    event({"type": "message_start", "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "401: DO-NOT-PUBLISH"}})
    event({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "401: DO-NOT-PUBLISH"}})
    event({"type": "turn_end"})
    event({"type": "agent_settled"})
    sys.exit(1)
if prompt == "provider-missing":
    event({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "Unknown provider: DO-NOT-PUBLISH"}})
    event({"type": "agent_settled"})
    sys.exit(1)
if prompt == "mixed-failure":
    event({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "Unknown provider: DO-NOT-PUBLISH"}})
    event({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": "model not found"}})
    event({"type": "agent_settled"})
    sys.exit(1)
if prompt == "malformed":
    print("not json", flush=True)
    sys.exit(0)
usage = {"input": 3, "output": 1, "totalTokens": 4}
event({"type": "message_start", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}], "usage": usage}})
event({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}], "usage": usage, "stopReason": "stop"}})
event({"type": "turn_end"})
event({"type": "agent_settled"})
"""
    )
    path.chmod(0o700)
    return path


def manager(tmp_path, executable, authorize=lambda _: True):
    return LocalExecutionManager(
        tmp_path / "receipts", PiRuntime(executable), authorize=authorize
    )


async def done(m, execution_id="execution-1"):
    async with asyncio.timeout(5):
        events = [event async for event in m.events(execution_id)]
    return await m.reconcile(execution_id), events


def calls(inv):
    return [
        json.loads(line)
        for line in (inv.workspace / "calls.jsonl").read_text().splitlines()
    ]


async def test_success_extracts_text_usage_and_events(tmp_path, invocation, executable):
    m = manager(tmp_path, executable)
    try:
        receipt = await m.start(invocation)
        final, events = await done(m)
        assert final.outcome == "succeeded"
        assert final.text == "answer"
        assert final.usage == {"input_tokens": 3, "output_tokens": 1}
        assert final.process_state == "finished"
        assert receipt.text is None  # start returns the queued receipt only
        kinds = [event.kind for event in events]
        assert "progress" in kinds
        assert {"event": "message_start", "role": "assistant"} in [
            event.payload for event in events if event.kind == "progress"
        ]
    finally:
        await m.close()


async def test_second_execution_resumes_native_session(tmp_path, invocation, executable):
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m, "execution-1")
        await m.start(replace(invocation, execution_id="execution-2"))
        await done(m, "execution-2")
        resumed = calls(invocation)[1]
        assert "--resume" in resumed["argv"]
        assert resumed["argv"][resumed["argv"].index("--resume") + 1] == (
            "native-pi-session"
        )
    finally:
        await m.close()


@pytest.mark.parametrize(
    ("prompt", "reason"),
    [
        ("failed", "ENGINE_AUTH_ERROR"),
        ("provider-missing", "PI_PROVIDER_ERROR"),
        ("mixed-failure", "ENGINE_ERROR"),
    ],
)
async def test_provider_stop_reason_is_classified_without_raw_message(
    tmp_path, invocation, executable, prompt, reason
):
    invocation = replace(invocation, prompt=prompt)
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        final, events = await done(m)
        assert final.outcome == "failed"
        assert final.reason == reason
        assert final.error_code == reason
        # The raw provider error message must not surface in text or events.
        assert final.text is None
        assert "DO-NOT-PUBLISH" not in str((final, events))
    finally:
        await m.close()


async def test_prompt_travels_on_stdin_not_argv(tmp_path, invocation, executable):
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m)
        (record,) = calls(invocation)
        assert record["prompt"] == "success"
        joined = " ".join(record["argv"])
        assert "success" not in joined
        assert "--print" in record["argv"]
        assert "--provider" in record["argv"]
        assert "zai" in record["argv"]
        assert "--model" in record["argv"]
        assert "glm-5.3-flash" in record["argv"]
    finally:
        await m.close()


async def test_environment_is_sandboxed(tmp_path, invocation, executable):
    invocation = replace(
        invocation,
        environment={"ZAI_API_KEY": "staged-only", "PATH": os.environ["PATH"]},
    )
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m)
        (record,) = calls(invocation)
        env = record["env"]
        home = str(invocation.runtime_home)
        assert env["HOME"] == home
        # Env split contract (architect + PM 2026-09-22): install asset
        # override REMOVED (CLI resolves its own executable), user config
        # parent and session dir are isolated under the sandbox home, and
        # the historical non-keys never appear.
        assert "PI_PACKAGE_DIR" not in env
        assert "PI_CONFIG_DIR" not in env
        assert "PI_SESSION_DIR" not in env
        assert env["PI_CODING_AGENT_DIR"] == str(
            invocation.runtime_home / ".pi" / "agent"
        )
        assert env["PI_CODING_AGENT_SESSION_DIR"] == str(
            invocation.runtime_home / "sessions"
        )
        assert env["PI_CODING_AGENT_DIR"] != env["PI_CODING_AGENT_SESSION_DIR"]
        assert env["PI_CODING_AGENT_SESSION_DIR"] == f"{home}/sessions"
        assert env["ZAI_API_KEY"] == "staged-only"
        assert not env.get("ANYGARDEN_HOME")
        assert not env.get("SLOCK_AGENT_ID")
    finally:
        await m.close()


async def test_rejects_ambient_server_credentials(tmp_path, invocation, executable):
    invocation = replace(invocation, environment={"SLOCK_AGENT_ID": "leak"})
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        final, _ = await done(m)
        # environment() raises on server-credential keys; run() falls back to
        # the safe unknown bucket rather than a precise code (as CodexRuntime).
        assert final.outcome == "unknown"
        # the process never launched: no call log exists
        assert not (invocation.workspace / "calls.jsonl").exists()
    finally:
        await m.close()


async def test_cancel_stops_hanging_process_tree(tmp_path, invocation, executable):
    invocation = replace(invocation, prompt="hang", timeout_seconds=5)
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        async with asyncio.timeout(3):
            while not (invocation.workspace / "child.pid").exists():
                await asyncio.sleep(0.01)
        pid = int((invocation.workspace / "child.pid").read_text())
        receipt = await m.cancel(invocation.execution_id)
        assert receipt.state == "cancel_requested"  # not proof of stop yet
        final, _ = await done(m)
        assert final.outcome == "cancelled"
        assert final.reason == "cancelled"
        assert final.process_state == "stopped"
        assert (
            not psutil.pid_exists(pid)
            or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        )
    finally:
        await m.close()


async def test_version_mismatch_is_unsupported(tmp_path, invocation, executable):
    executable.chmod(0o755)
    text = executable.read_text()
    executable.write_text(text.replace('"0.85.1"', '"0.99.9"'))
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        final, _ = await done(m)
        assert final.outcome == "failed"
        assert final.reason == "UNSUPPORTED_RUNTIME"
        assert final.process_state == "not_started"
    finally:
        await m.close()


async def test_missing_provider_is_contract_rejected(tmp_path, invocation, executable):
    invocation = replace(invocation, provider=None)
    with pytest.raises(ValueError):
        invocation.validate()
    runtime = PiRuntime(executable)
    with pytest.raises(ValueError):
        runtime.command(invocation, None, tmp_path / "out")


@pytest.mark.asyncio
async def test_usage_sums_across_multiple_assistant_messages(tmp_path):
    """P1 (architect fixture 2026-09-22): (10,2)+(20,3) must total (30,5).

    Overwriting kept only the last block — multi-tool turns under-reported
    usage. Uses a fake emitting two usage-bearing assistant messages.
    """
    workspace = tmp_path / "workspace"
    home = tmp_path / "runtime-home"
    workspace.mkdir()
    home.mkdir()
    fake = tmp_path / "fake-pi-multi"
    fake.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, sys
if sys.argv[1:] == ["--version"]:
    print("pi 0.85.1")
    sys.exit(0)
sys.stdin.read()
def emit(v):
    print(json.dumps(v), flush=True)
emit({"type": "session", "version": 3, "id": "native-pi-session"})
emit({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "part-1"}], "usage": {"input": 10, "output": 2}}})
emit({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "part-2"}], "usage": {"input": 20, "output": 3}}})
emit({"type": "agent_settled"})
"""
    )
    fake.chmod(0o700)
    inv = Invocation(
        "execution-1",
        SessionScope(
            "node-a", "agent-1", "node-a", "room", None,
            "managed-workspace", 1, 1, engine="pi-cli", engine_version="0.85.1",
        ),
        "multi",
        workspace,
        home,
        provider="zai",
        model="glm-5.3-flash",
        timeout_seconds=5,
    )
    m = manager(tmp_path, fake)
    try:
        await m.start(inv)
        receipt, _events = await done(m)
        assert receipt.outcome == "succeeded"
        assert receipt.usage == {"input_tokens": 30, "output_tokens": 5}
    finally:
        await m.close()


async def test_version_mismatch_reports_observed_and_expected(
    tmp_path, invocation, executable
):
    text = executable.read_text()
    executable.write_text(text.replace('"0.85.1"', '"0.87.1"'))
    runtime = PiRuntime(executable)
    m = LocalExecutionManager(tmp_path / "receipts", runtime, authorize=lambda _: True)
    try:
        await m.start(invocation)
        final, _ = await done(m)
        assert final.reason == "UNSUPPORTED_RUNTIME"
    finally:
        await m.close()
    assert runtime.observed_version == "0.87.1"
    assert runtime.unsupported_detail() == (
        "pi-cli 0.87.1 is not supported; this build requires 0.85.1"
    )


def test_unsupported_detail_without_observed_version(tmp_path):
    runtime = PiRuntime(tmp_path / "missing-pi")
    assert runtime.observed_version is None
    assert runtime.unsupported_detail() == (
        "could not read the installed pi-cli version; this build requires 0.85.1"
    )
