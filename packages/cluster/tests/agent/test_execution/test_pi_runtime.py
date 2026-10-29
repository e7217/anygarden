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
import structlog

from anygarden.agent.runtime.execution import (
    Invocation,
    LocalExecutionManager,
    SessionScope,
)
from anygarden.agent.runtime.execution.pi import PiRuntime

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
if prompt == "stall":
    time.sleep(60)  # times out before any tool ran
if prompt == "unknown-stall":
    # An event type this adapter does not know (e.g. a newer Pi release):
    # it may stand for work with effects, so the timeout is not transient.
    event({"type": "session", "id": "s1"})
    event({"type": "agent_start"})
    event({"type": "extension_action_start", "name": "deploy"})
    time.sleep(60)
if prompt == "safe-stall":
    # Only known side-effect-free events: the model was still thinking.
    event({"type": "session", "id": "s1"})
    event({"type": "agent_start"})
    event({"type": "turn_start"})
    event({"type": "message_start", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}})
    event({"type": "message_end", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}})
    event({"type": "message_start", "message": {"role": "assistant", "content": []}})
    event({"type": "message_update", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Work"}]}})
    time.sleep(60)
def finish():
    usage = {"input": 3, "output": 1, "totalTokens": 4}
    event({"type": "message_start", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}], "usage": usage}})
    event({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}], "usage": usage, "stopReason": "stop"}})
    event({"type": "turn_end"})
    event({"type": "agent_settled"})
    sys.exit(0)
if prompt == "chatty":
    # #892 — keeps reporting progress for longer than the idle limit.
    for _ in range(12):
        event({"type": "message_update", "message": {"role": "assistant", "content": []}, "assistantMessageEvent": {"type": "text_delta", "delta": "x"}})
        time.sleep(0.1)
    finish()
if prompt == "tool-silent":
    # #892 — a long command prints nothing while it runs.
    event({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "bash", "args": {}})
    time.sleep(1.0)
    event({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "bash", "result": "", "isError": False})
    finish()
if prompt == "failed-edit-stall":
    # A failed edit emits no "completed" activity; it must not pin the turn.
    event({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "edit", "args": {}})
    event({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "edit", "result": "", "isError": True})
    time.sleep(60)
if prompt == "tool-done-stall":
    event({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "bash", "args": {}})
    event({"type": "tool_execution_end", "toolCallId": "t1", "toolName": "bash", "result": "", "isError": False})
    time.sleep(60)
if prompt in {"tool-stall", "toolcall-stall"}:
    if prompt == "tool-stall":
        event({"type": "tool_execution_start", "toolCallId": "t1", "toolName": "bash", "args": {}})
    else:
        event({"type": "message_end", "message": {"role": "assistant", "content": [{"type": "toolCall", "id": "t1", "name": "bash", "arguments": {}}], "stopReason": "toolUse"}})
    time.sleep(60)
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
if prompt == "big-tool-call":
    # #974 — a tool call with a large argument is one oversized event line.
    event({"type": "tool_execution_start", "toolCallId": "t", "toolName": "write", "args": {"content": "DO-NOT-PUBLISH" * 400000}})
    event({"type": "tool_execution_end", "toolCallId": "t", "toolName": "write", "result": "ok", "isError": False})
if prompt in {"multi-step", "ends-on-tool"}:
    # Event shapes from pi-agent-core 0.85.1 (types.d.ts), raw values replaced.
    def assistant(content, stop):
        msg = {"role": "assistant", "content": content, "stopReason": stop}
        event({"type": "message_start", "message": {"role": "assistant", "content": []}})
        event({"type": "message_end", "message": msg})
    def tool(name, is_error=False):
        event({"type": "tool_execution_start", "toolCallId": "t", "toolName": name, "args": {"command": "DO-NOT-PUBLISH"}})
        event({"type": "tool_execution_end", "toolCallId": "t", "toolName": name, "result": "DO-NOT-PUBLISH", "isError": is_error})
    event({"type": "message_update", "message": {"role": "assistant", "content": []}, "assistantMessageEvent": {"type": "thinking_start", "contentIndex": 0}})
    event({"type": "message_update", "message": {"role": "assistant", "content": []}, "assistantMessageEvent": {"type": "thinking_end", "contentIndex": 0, "content": "DO-NOT-PUBLISH"}})
    assistant([{"type": "thinking", "thinking": "DO-NOT-PUBLISH"}, {"type": "text", "text": "I will list the files."}, {"type": "toolCall", "id": "t", "name": "ls", "arguments": {}}], "toolUse")
    tool("ls")
    assistant([{"type": "text", "text": "Now editing."}, {"type": "toolCall", "id": "t", "name": "edit", "arguments": {}}], "toolUse")
    tool("edit")
    tool("write", is_error=True)
    tool("bash")
    tool("read_room_messages")
    tool("DO-NOT-PUBLISH")
    if prompt == "multi-step":
        assistant([{"type": "text", "text": "Done."}, {"type": "text", "text": "All good."}], "stop")
    event({"type": "turn_end"})
    event({"type": "agent_settled"})
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


async def test_oversized_event_line_is_dropped_and_turn_succeeds(
    tmp_path, invocation, executable
):
    """#974 — a >4 MiB event line is skipped instead of ending the turn."""
    with structlog.testing.capture_logs() as logs:
        m = manager(tmp_path, executable)
        try:
            await m.start(replace(invocation, prompt="big-tool-call"))
            final, events = await done(m)
        finally:
            await m.close()
    assert final.outcome == "succeeded" and final.text == "answer"
    dropped = [e for e in logs if e["event"] == "engine_output.line_dropped"]
    assert [e["event_type"] for e in dropped] == ["tool_execution_start"]
    assert "DO-NOT-PUBLISH" not in str((final, events, logs))


async def test_collect_failure_is_logged_with_its_class(
    tmp_path, invocation, executable, monkeypatch
):
    """#974 — invalid_runtime_output names the exception in the agent log."""
    async def broken(self, *args, **kwargs):
        raise RuntimeError("boom DO-NOT-PUBLISH-" + "x" * 500)

    monkeypatch.setattr(PiRuntime, "_collect", broken)
    with structlog.testing.capture_logs() as logs:
        m = manager(tmp_path, executable)
        try:
            await m.start(invocation)
            final, _ = await done(m)
        finally:
            await m.close()
    assert final.reason == "invalid_runtime_output"
    [entry] = [e for e in logs if e["event"] == "engine_output.invalid"]
    assert entry["engine"] == "pi-cli" and entry["error"] == "RuntimeError"
    assert len(entry["detail"]) <= 200


@pytest.mark.req("ENG-10")
async def test_multi_step_turn_emits_normalized_activity_and_narration(
    tmp_path, invocation, executable
):
    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt="multi-step"))
        final, events = await done(m)
    finally:
        await m.close()
    assert final.outcome == "succeeded"
    # The reply is the text after the last tool work, not every message joined.
    assert final.text == "Done.\nAll good."
    normalized = [
        e.payload for e in events
        if e.kind == "progress" and e.payload["event"] in {"activity", "narration"}
    ]
    assert normalized == [
        {"event": "activity", "activity": "thinking", "phase": "started"},
        {"event": "activity", "activity": "thinking", "phase": "completed", "count": 1},
        {"event": "narration", "text": "I will list the files."},
        {"event": "activity", "activity": "file_read", "phase": "started"},
        {"event": "activity", "activity": "file_read", "phase": "completed", "count": 1},
        {"event": "narration", "text": "Now editing."},
        {"event": "activity", "activity": "file_change", "phase": "started"},
        {"event": "activity", "activity": "file_change", "phase": "completed", "count": 1},
        {"event": "activity", "activity": "file_change", "phase": "started"},
        {"event": "activity", "activity": "command", "phase": "started"},
        {"event": "activity", "activity": "command", "phase": "completed", "count": 1},
        {"event": "activity", "activity": "anygarden_tool", "phase": "started"},
        {"event": "activity", "activity": "anygarden_tool", "phase": "completed", "count": 1},
        {"event": "activity", "activity": "tool", "phase": "started"},
        {"event": "activity", "activity": "tool", "phase": "completed", "count": 1},
    ]
    assert "DO-NOT-PUBLISH" not in str(events)


@pytest.mark.req("ENG-11")
async def test_turn_ending_on_tool_work_replies_with_its_last_narration(
    tmp_path, invocation, executable
):
    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt="ends-on-tool"))
        final, _ = await done(m)
    finally:
        await m.close()
    assert final.outcome == "succeeded"
    assert final.text == "Now editing."


async def test_second_execution_resumes_native_session(tmp_path, invocation, executable):
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m, "execution-1")
        await m.start(replace(invocation, execution_id="execution-2"))
        await done(m, "execution-2")
        resumed = calls(invocation)[1]
        # Pi's --resume is a bare flag that opens the interactive selector;
        # --session <id> is the option that opens a specific session (#714).
        assert "--resume" not in resumed["argv"]
        assert resumed["argv"][resumed["argv"].index("--session") + 1] == (
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


@pytest.mark.req("EXE-10")
@pytest.mark.parametrize(
    ("prompt", "reason", "transient"),
    [
        ("stall", "TIMEOUT_STOPPED", True),
        # #892 — a tool that ran (even a failed edit) and then silence.
        ("tool-done-stall", "TIMEOUT_AFTER_SIDE_EFFECT", False),
        ("failed-edit-stall", "TIMEOUT_AFTER_SIDE_EFFECT", False),
        # A tool call the engine never started is not an open tool.
        ("toolcall-stall", "TIMEOUT_AFTER_SIDE_EFFECT", False),
        # Fail closed (EXE-10): only known side-effect-free events keep a
        # timeout transient; any unrecognised event type may be an effect.
        ("safe-stall", "TIMEOUT_STOPPED", True),
        ("unknown-stall", "TIMEOUT_AFTER_SIDE_EFFECT", False),
    ],
)
async def test_timeout_is_transient_only_before_any_tool(
    tmp_path, invocation, executable, prompt, reason, transient
):
    from anygarden.agent.runtime.execution.failure_feedback import (
        terminal_failure_category,
    )

    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt=prompt, timeout_seconds=0.3))
        receipt, _ = await done(m)
        assert (receipt.outcome, receipt.reason) == ("failed", reason)
        assert terminal_failure_category(receipt.outcome, receipt.reason)[1] is transient
    finally:
        await m.close()


@pytest.mark.req("ENG-04")
@pytest.mark.parametrize("prompt", ["chatty", "tool-silent"])
async def test_active_turn_outlives_the_idle_limit(
    tmp_path, invocation, executable, prompt
):
    """#892 — progress or an open tool keeps a turn past its idle limit."""
    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt=prompt, timeout_seconds=0.5))
        receipt, _ = await done(m)
        assert (receipt.outcome, receipt.reason) == ("succeeded", "completed")
        assert receipt.text == "answer"
    finally:
        await m.close()


async def test_stalled_open_tool_waits_for_stop(tmp_path, invocation, executable):
    """#892 — a silent open tool never idles out; Stop cleans it up."""
    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt="tool-stall", timeout_seconds=0.3))
        await asyncio.sleep(1.0)
        assert m._store.get(invocation.execution_id).outcome is None
        await m.cancel(invocation.execution_id)
        receipt, _ = await done(m)
        assert (receipt.outcome, receipt.process_state) == ("cancelled", "stopped")
    finally:
        await m.close()


@pytest.mark.req("EXE-10")
def test_safe_event_list_was_reviewed_for_the_pinned_pi_version():
    """The fail-closed safe-event list is only valid for the Pi it was read
    against. Bumping the pinned engine must force a re-review of the list."""
    from anygarden.agent.runtime.execution import pi

    assert pi.PI_SAFE_EVENTS_REVIEWED_FOR == pi.ENGINE_VERSION, (
        f"Pi is pinned to {pi.ENGINE_VERSION} but the side-effect-free event "
        f"list (_SAFE_EVENTS, _SAFE_MESSAGE_ROLES, _SAFE_CONTENT_BLOCKS in "
        f"anygarden/agent/runtime/execution/pi.py) was reviewed for "
        f"{pi.PI_SAFE_EVENTS_REVIEWED_FOR}. Re-check Pi {pi.ENGINE_VERSION}'s "
        f"JSON event types and message/content shapes, update the lists, then "
        f"set PI_SAFE_EVENTS_REVIEWED_FOR = {pi.ENGINE_VERSION!r}."
    )
