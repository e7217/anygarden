"""Reviewed task94 regression imported from c1aa272 (synchronous cancel)."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest
from anygarden_agent.runtime.execution import (
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
        "node-a",
        "agent-1",
        "node-a",
        "room",
        None,
        "managed-workspace",
        1,
        1,
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


def write_exec(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content.replace("{sys.executable}", sys.executable))
    path.chmod(0o700)
    return path


async def drain(m, execution_id):
    async with asyncio.timeout(10):
        events = [e async for e in m.events(execution_id)]
    return await m.reconcile(execution_id), events


@pytest.mark.asyncio
async def test_r_m_cancel_after_turn_end_preserves_usage(tmp_path, monkeypatch):
    """Cancel only after synchronous progress proves measured usage was read."""
    exe = write_exec(tmp_path, "fake-pi", PI_FAKE_HANG)
    inv = invocation("pi-cli", tmp_path, provider="zai", prompt="hang")
    turn_end_seen = asyncio.Event()
    runtime = PiRuntime(exe)
    m = LocalExecutionManager(
        tmp_path / "receipts-pi", runtime, authorize=lambda _: True
    )
    original_collect = runtime._collect

    async def collect_spy(proc, inv_, session, output, emit, authorized):
        def emit_spy(kind, event):
            emit(kind, event)
            if kind == "progress" and event.get("event") == "turn_end":
                turn_end_seen.set()

        return await original_collect(proc, inv_, session, output, emit_spy, authorized)

    monkeypatch.setattr(runtime, "_collect", collect_spy)
    try:
        await m.start(inv)
        await asyncio.wait_for(turn_end_seen.wait(), timeout=5)
        await m.cancel(inv.execution_id)
        receipt, events = await drain(m, inv.execution_id)
        assert receipt.outcome == "cancelled"
        assert [event.kind for event in events].count("terminal") == 1
        assert receipt.usage == {"input_tokens": 3, "output_tokens": 1}
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
event({"type": "turn_end"})
import signal
signal.pause()
""".replace("{sys.executable}", sys.executable)
