"""Exercise the product manager and subprocess boundary without a provider."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace

import psutil
import pytest

from anygarden_agent.runtime.execution import (
    CodexRuntime,
    ExecutionConflict,
    Invocation,
    LocalExecutionManager,
    SessionScope,
)
from anygarden_agent.runtime.execution.codex import ProcessTree
from anygarden_agent.runtime.execution.store import ReceiptStore

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
            "node-a", "agent-1", "node-a", "room", None, "managed-workspace", 1, 1
        ),
        "success",
        workspace,
        home,
        timeout_seconds=2,
    )


@pytest.fixture
def executable(tmp_path):
    path = tmp_path / "fake-codex"
    path.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, signal, subprocess, sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("codex-cli " + os.environ.get("FAKE_VERSION", "0.154.0"))
    sys.exit(0)
prompt = sys.stdin.read()
with Path("calls.jsonl").open("a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "env": dict(os.environ)}) + "\n")
def event(value):
    print(json.dumps(value), flush=True)
if prompt == "hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, "-c", "import time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])
    Path("child.pid").write_text(str(child.pid))
event({"type": "thread.started", "thread_id": "native-session"})
event({"type": "turn.started"})
event({"type": "item.started", "item": {"type": "command_execution", "command": "DO-NOT-PUBLISH"}})
if prompt == "hang":
    time.sleep(60)
if prompt == "delay":
    time.sleep(0.15)
if prompt == "partial":
    Path("effect").write_text("already executed")
    sys.exit(1)
if prompt == "failed":
    event({"type": "turn.failed", "error": {"message": "DO-NOT-PUBLISH"}})
    sys.exit(1)
if prompt == "malformed":
    print("not json", flush=True)
    sys.exit(0)
if prompt == "huge":
    print("X" * 1200000, flush=True)
    sys.exit(0)
event({"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}})
event({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}})
Path(sys.argv[sys.argv.index("-o") + 1]).write_text("answer")
"""
    )
    path.chmod(0o700)
    return path


def manager(tmp_path, executable, authorize=lambda _: True):
    return LocalExecutionManager(
        tmp_path / "receipts", CodexRuntime(executable), authorize=authorize
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


async def test_progress_result_duplicate_and_restart(tmp_path, executable, invocation):
    m = manager(tmp_path, executable)
    try:
        assert (await m.start(invocation)).state == "queued"
        await m.start(invocation)
        receipt, events = await done(m)
        assert receipt.outcome == "succeeded" and receipt.text == "answer"
        assert receipt.usage == {"input_tokens": 3, "output_tokens": 1}
        assert [e.kind for e in events].count("terminal") == 1
        assert any(e.kind == "progress" for e in events)
        assert "DO-NOT-PUBLISH" not in str(events)
        assert len(calls(invocation)) == 1
        assert await m.start(invocation) == receipt
        with pytest.raises(ExecutionConflict):
            await m.start(replace(invocation, prompt="changed"))
    finally:
        await m.close()
    m = manager(tmp_path, executable)
    try:
        assert (await m.start(invocation)).outcome == "succeeded"
        await m.start(replace(invocation, execution_id="execution-2"))
        await done(m, "execution-2")
        assert calls(invocation)[1]["argv"][1:3] == ["resume", "native-session"]
    finally:
        await m.close()


async def test_partial_resume_failure_never_reexecutes(
    tmp_path, executable, invocation
):
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m)
        partial = replace(invocation, execution_id="partial", prompt="partial")
        await m.start(partial)
        receipt, _ = await done(m, "partial")
        assert receipt.outcome == "unknown" and receipt.process_state == "stopped"
        assert (invocation.workspace / "effect").exists()
        await m.start(partial)
        assert len(calls(invocation)) == 2
        await m.start(replace(invocation, execution_id="blocked-next"))
        blocked, _ = await done(m, "blocked-next")
        assert blocked.reason == "POLICY_DENIED"
        assert len(calls(invocation)) == 2
    finally:
        await m.close()


@pytest.mark.parametrize(
    ("prompt", "outcome", "reason"),
    [
        ("failed", "failed", "ENGINE_ERROR"),
        ("malformed", "unknown", "missing_terminal_event"),
        ("huge", "unknown", "invalid_runtime_output"),
    ],
)
async def test_terminal_failure_categories(
    tmp_path, executable, invocation, prompt, outcome, reason
):
    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt=prompt))
        receipt, _ = await done(m)
        assert receipt.outcome == outcome and receipt.reason == reason
        assert receipt.text is None
    finally:
        await m.close()


@pytest.mark.parametrize("timeout", [False, True])
async def test_cancel_and_timeout_reap_tool_tree(
    tmp_path, executable, invocation, timeout
):
    m = manager(tmp_path, executable)
    try:
        await m.start(
            replace(invocation, prompt="hang", timeout_seconds=0.3 if timeout else 5)
        )
        async with asyncio.timeout(3):
            while not (invocation.workspace / "child.pid").exists():
                await asyncio.sleep(0.01)
        pid = int((invocation.workspace / "child.pid").read_text())
        if not timeout:
            receipt = await m.cancel(invocation.execution_id)
            assert receipt.state == "cancel_requested"  # not proof of stop yet
        receipt, _ = await done(m)
        assert receipt.outcome == ("failed" if timeout else "cancelled")
        assert receipt.reason == ("TIMEOUT_STOPPED" if timeout else "cancelled")
        assert receipt.process_state == "stopped"
        assert (
            not psutil.pid_exists(pid)
            or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        )
    finally:
        await m.close()


async def test_unconfirmed_stop_is_unknown(
    tmp_path, executable, invocation, monkeypatch
):
    original = ProcessTree.stop

    async def cannot_confirm(self):
        await original(self)
        return False

    monkeypatch.setattr(ProcessTree, "stop", cannot_confirm)
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        receipt, _ = await done(m)
        assert receipt.outcome == "unknown" and receipt.process_state == "unknown"
        assert receipt.text is None
    finally:
        await m.close()


async def test_session_fifo_and_cancel_before_launch(tmp_path, executable, invocation):
    m = manager(tmp_path, executable)
    try:
        await m.start(replace(invocation, prompt="delay"))
        await m.start(replace(invocation, execution_id="queued"))
        await m.cancel("queued")
        receipt, _ = await done(m, "queued")
        assert receipt.outcome == "cancelled" and receipt.process_state == "not_started"
        await done(m)
        assert len(calls(invocation)) == 1
    finally:
        await m.close()


async def test_revoke_rechecks_queued_and_duplicate_access(
    tmp_path, executable, invocation
):
    allowed = True
    m = manager(tmp_path, executable, authorize=lambda _: allowed)
    try:
        await m.start(invocation)
        await done(m)
        allowed = False
        with pytest.raises(PermissionError):
            await m.start(invocation)
        with pytest.raises(PermissionError):
            await m.reconcile(invocation.execution_id)
        allowed = True
        await m.start(replace(invocation, execution_id="queued"))
        allowed = False
        await asyncio.sleep(0.05)
        allowed = True
        receipt, _ = await done(m, "queued")
        assert receipt.reason == "POLICY_DENIED"
        assert len(calls(invocation)) == 1
    finally:
        await m.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("agent_id", "other"),
        ("channel_id", "other"),
        ("authority_node_id", "other"),
        ("thread_root_id", "thread"),
        ("workspace_epoch", 2),
        ("policy_epoch", 2),
    ],
)
async def test_session_scope_isolation(tmp_path, executable, invocation, field, value):
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m)
        await m.start(
            replace(
                invocation,
                execution_id="other",
                scope=replace(invocation.scope, **{field: value}),
            )
        )
        await done(m, "other")
        assert "resume" not in calls(invocation)[1]["argv"]
    finally:
        await m.close()


async def test_no_ambient_environment_or_legacy_session_import(
    tmp_path, executable, invocation, monkeypatch
):
    monkeypatch.setenv("SERVER_SECRET", "must-not-inherit")
    (invocation.workspace / ".anygarden-engine-sessions.json").write_text(
        '{"room":"legacy-private"}'
    )
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m)
        call = calls(invocation)[0]
        assert "SERVER_SECRET" not in call["env"]
        assert call["env"]["HOME"] == str(invocation.runtime_home)
        assert "resume" not in call["argv"]
        assert "--ignore-user-config" in call["argv"]
        assert (invocation.workspace / ".anygarden-engine-sessions.json").exists()
    finally:
        await m.close()


@pytest.mark.parametrize(
    "state,process,outcome",
    [
        ("launching", "unknown", "unknown"),
        ("running", "running", "unknown"),
        ("queued", "not_started", "cancelled"),
    ],
)
async def test_crash_after_intent_does_not_spawn(
    tmp_path, executable, invocation, state, process, outcome
):
    store = ReceiptStore(tmp_path / "receipts")
    store.accept(invocation)
    if state != "queued":
        store.transition(invocation.execution_id, state, process)
    store.close()
    m = manager(tmp_path, executable)
    try:
        assert (await m.start(invocation)).outcome == outcome
        assert not (invocation.workspace / "calls.jsonl").exists()
    finally:
        await m.close()


async def test_single_owner_and_version_gate(tmp_path, executable, invocation):
    m = manager(tmp_path, executable)
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            manager(tmp_path, executable)
        await m.start(replace(invocation, environment={"FAKE_VERSION": "0.146.0"}))
        receipt, _ = await done(m)
        assert receipt.reason == "UNSUPPORTED_RUNTIME"
        assert receipt.process_state == "not_started"
        assert not (invocation.workspace / "calls.jsonl").exists()
    finally:
        await m.close()


def test_import_does_not_load_chat_client():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import anygarden_agent.runtime.execution; assert 'anygarden_agent.client' not in sys.modules",
        ],
        check=True,
    )


async def test_cancel_during_spawn_is_not_lost(
    tmp_path, executable, invocation, monkeypatch
):
    real_spawn = asyncio.create_subprocess_exec
    child_created = asyncio.Event()
    release_spawn = asyncio.Event()

    async def slow_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        if "--version" not in args:
            child_created.set()
            await release_spawn.wait()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow_spawn)
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await asyncio.wait_for(child_created.wait(), 3)
        assert (await m.cancel(invocation.execution_id)).state == "cancel_requested"
        release_spawn.set()
        receipt, events = await done(m)
        assert receipt.outcome == "cancelled"
        assert receipt.process_state == "stopped"
        assert len([e for e in events if e.kind == "terminal"]) == 1
        assert not (invocation.workspace / "calls.jsonl").exists()
    finally:
        release_spawn.set()
        await m.close()


async def test_same_session_fifo_resumes_and_revoke_stops(
    tmp_path, executable, invocation
):
    allowed = True
    m = manager(tmp_path, executable, authorize=lambda _: allowed)
    try:
        await m.start(replace(invocation, prompt="delay"))
        await m.start(replace(invocation, execution_id="second"))
        await done(m, "second")
        assert calls(invocation)[1]["argv"][1:3] == ["resume", "native-session"]
        await m.start(replace(invocation, execution_id="third", prompt="hang"))
        async with asyncio.timeout(3):
            while not (invocation.workspace / "child.pid").exists():
                await asyncio.sleep(0.01)
        allowed = False
        await m.revoke(invocation.scope)
        await asyncio.gather(*list(m._tasks.values()))
        assert m._store.get("third").outcome == "cancelled"
        assert m._store.session(invocation.scope.key) is None
    finally:
        await m.close()


async def test_legacy_resume_nonzero_does_not_retry(
    tmp_path, executable, invocation, monkeypatch
):
    from anygarden_agent.integrations.codex_cli import CodexCliAdapter
    from anygarden_agent.runtime.handler_wrapper import EngineError

    monkeypatch.chdir(invocation.workspace)
    monkeypatch.setattr(os, "environ", {})
    adapter = CodexCliAdapter()
    adapter._codex_path = str(executable)
    adapter._room_thread_ids["room"] = "already-started"
    with pytest.raises(EngineError) as error:
        await adapter._call_codex("partial", "room")
    assert error.value.transient is False
    assert len(calls(invocation)) == 1
    assert (invocation.workspace / "effect").exists()


async def test_new_materialization_requires_epoch_and_external_paths_rejected(
    tmp_path, executable, invocation
):
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await done(m)
        with pytest.raises(ExecutionConflict):
            await m.start(
                replace(
                    invocation, execution_id="other", workspace=invocation.runtime_home
                )
            )
        with pytest.raises(ValueError, match="external workspace"):
            await m.start(
                replace(invocation, execution_id="other", external_workspace=True)
            )
    finally:
        await m.close()


async def test_revocation_during_version_probe_prevents_spawn(
    tmp_path, executable, invocation, monkeypatch
):
    allowed = True
    original = CodexRuntime._version_matches

    async def check(self, inv, env):
        nonlocal allowed
        result = await original(self, inv, env)
        allowed = False
        return result

    monkeypatch.setattr(CodexRuntime, "_version_matches", check)
    m = manager(tmp_path, executable, authorize=lambda _: allowed)
    try:
        await m.start(invocation)
        await asyncio.gather(*list(m._tasks.values()))
        allowed = True
        receipt, _ = await done(m)
        assert (
            receipt.reason == "POLICY_DENIED" and receipt.process_state == "not_started"
        )
        assert not (invocation.workspace / "calls.jsonl").exists()
    finally:
        await m.close()


async def test_cancel_during_exit_cleanup_preserves_reaping(
    tmp_path, executable, invocation, monkeypatch
):
    entered = asyncio.Event()
    proceed = asyncio.Event()
    original = ProcessTree.stop

    async def delayed_stop(self):
        entered.set()
        await proceed.wait()
        return await original(self)

    monkeypatch.setattr(ProcessTree, "stop", delayed_stop)
    m = manager(tmp_path, executable)
    try:
        await m.start(invocation)
        await asyncio.wait_for(entered.wait(), 3)
        assert (await m.cancel(invocation.execution_id)).state == "cancel_requested"
        proceed.set()
        receipt, _ = await done(m)
        assert receipt.outcome == "cancelled" and receipt.process_state == "stopped"
        assert receipt.text is None
    finally:
        proceed.set()
        await m.close()


async def test_revocation_at_collect_scheduling_never_writes_prompt(
    tmp_path, executable, invocation, monkeypatch
):
    allowed = True
    writes = []
    original_spawn = asyncio.create_subprocess_exec
    original_collect = CodexRuntime._collect
    m = manager(tmp_path, executable, authorize=lambda _: allowed)

    async def spy_spawn(*args, **kwargs):
        proc = await original_spawn(*args, **kwargs)
        if proc.stdin is not None:
            original_write = proc.stdin.write

            def write(data):
                writes.append((allowed, m._store.get(invocation.execution_id).state))
                return original_write(data)

            proc.stdin.write = write
        return proc

    async def revoked_collect(self, *args, **kwargs):
        nonlocal allowed
        # This coroutine is scheduled after the parent run() last checks authority.
        # revoke records cancel_requested, then yields before cancelling the parent.
        allowed = False
        await m.revoke(invocation.scope)
        return await original_collect(self, *args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy_spawn)
    monkeypatch.setattr(CodexRuntime, "_collect", revoked_collect)
    try:
        await m.start(invocation)
        await asyncio.gather(*list(m._tasks.values()))
        assert writes == [], f"prompt delivered after revocation: {writes}"
        assert m._store.get(invocation.execution_id).outcome in {"cancelled", "failed"}
    finally:
        await m.close()


def test_contracts_engine_membership_and_provider_rules(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "runtime-home"
    workspace.mkdir()
    home.mkdir()
    base = {
        "execution_node_id": "node-a",
        "agent_id": "agent-1",
        "authority_node_id": "node-a",
        "channel_id": "room",
        "thread_root_id": None,
        "workspace_binding_id": "managed-workspace",
        "workspace_epoch": 1,
        "policy_epoch": 1,
    }

    def make(engine, engine_version="1.0.0", provider=None, model=None):
        return Invocation(
            "execution-x",
            SessionScope(**base, engine=engine, engine_version=engine_version),
            "success",
            workspace,
            home,
            timeout_seconds=2,
            model=model,
            provider=provider,
        )

    # Membership is central; each adapter owns version authority.
    make("codex-cli").validate()
    make("pi-cli", provider="zai", model="glm-5.3-flash").validate()
    with pytest.raises(ValueError, match="unsupported runtime"):
        make("claude-cli").validate()

    # pi-cli never falls back to an ambient default provider.
    with pytest.raises(ValueError, match="explicit provider"):
        make("pi-cli").validate()

    # Provider must be a plain identifier when set.
    with pytest.raises(ValueError, match="plain identifier"):
        make("codex-cli", provider="zai --print").validate()

    # The provider choice is bound into the invocation fingerprint.
    assert make("pi-cli", provider="zai").fingerprint != make(
        "pi-cli", provider="other"
    ).fingerprint
