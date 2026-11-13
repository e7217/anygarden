"""#1046 — a turn cut off without a known result no longer silences the room."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from anygarden.agent.integrations.room_execution import (
    _SESSION_RECOVERED_NOTICE,
    RoomExecutionAdapter,
)
from anygarden.agent.runtime.execution import CodexRuntime, LocalExecutionManager
from anygarden.agent.runtime.execution.contracts import RuntimeResult
from anygarden.agent.runtime.execution.launch import ExecutionLaunch
from anygarden.agent.runtime.execution.room import RoomInvocation

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX execution store")

ROOM = "room-1"


@pytest.fixture
def adapter(tmp_path):
    executable = tmp_path / "fake-codex"
    executable.write_text(f"#!{sys.executable}\n")
    adapter = RoomExecutionAdapter()
    adapter._client = SimpleNamespace(
        _server_url="wss://cluster.example", _agent_id="agent", _generation=1,
        _room_memory={},
    )
    adapter._root = tmp_path
    adapter._launch = ExecutionLaunch("codex-cli", None, "model", 1, None)
    adapter._runtime_version = "0.154.0"
    adapter._manager = LocalExecutionManager(
        tmp_path / "receipts", CodexRuntime(executable), authorize=lambda _: True
    )
    yield adapter
    adapter._manager._store.close()


def _leave_unknown_turn(adapter, msg, pid=None):
    """Record a turn the way a node restart leaves it: outcome unknown."""
    scope = adapter._launch.bind_scope(
        adapter._session_scope(msg, ROOM, adapter._workspace())
    )
    store = adapter._manager._store
    store.accept(RoomInvocation(
        execution_id="cut-off", scope=scope, prompt="p",
        workspace=adapter._workspace(), runtime_home=adapter._root,
    ))
    store.transition("cut-off", "running", "running", pid)
    with store.db:
        store.db.execute("INSERT INTO sessions VALUES (?,?)", (scope.key, "old-session"))
    store.finish("cut-off", RuntimeResult("unknown", "unknown", "manager_restarted"))
    return scope


@pytest.mark.req("ENG-16")
def test_a_dead_cut_off_turn_is_recovered_into_a_fresh_session(adapter):
    msg = {"room_id": ROOM, "content": "hi"}
    scope = _leave_unknown_turn(adapter, msg)
    assert adapter._manager.has_session(scope)

    assert adapter._recover_session(msg, ROOM) is True
    # The prompt is composed after this, so the turn re-seeds a fresh session.
    assert not adapter._has_resumable_session(msg)
    assert adapter._recover_session(msg, ROOM) is False


@pytest.mark.req("ENG-16")
def test_a_live_cut_off_turn_keeps_the_session_blocked(adapter):
    msg = {"room_id": ROOM, "content": "hi"}
    scope = _leave_unknown_turn(adapter, msg, pid=os.getpid())

    assert adapter._recover_session(msg, ROOM) is False
    assert adapter._manager._store.scope_blocked(scope.key)


@pytest.mark.req("ENG-16")
def test_the_recovering_reply_names_the_fresh_session(adapter):
    token = adapter._session_recovered.set(True)
    try:
        assert adapter._with_recovery_notice("answer") == (
            f"{_SESSION_RECOVERED_NOTICE}\n\nanswer"
        )
        assert adapter._with_recovery_notice("") == ""
    finally:
        adapter._session_recovered.reset(token)
    assert adapter._with_recovery_notice("answer") == "answer"
