"""Registered room handler -> fake child -> lifecycle writer -> SQLite budget.

The WS transport/lease handler is not exercised: captured production lifecycle
frames are passed to its existing writer. No real model or gateway is involved.
"""

import asyncio
import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anygarden.budgets.ledger import compute_observed_tokens
from anygarden.db.engine import build_session_factory
from anygarden.db.models import Agent, Project, Room, UsageLedger
from anygarden.ws.handler import _frame_carries_usage, _write_lifecycle_usage_row
from anygarden.ws.protocol import LifecycleFrame
from sqlalchemy import select

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX subprocess runtime")

# Reuse the reviewed agent fake executable and cleanup fixture across packages.
_fixture_path = (
    Path(__file__).resolve().parents[2] / "agent/tests/test_room_execution.py"
)
_spec = importlib.util.spec_from_file_location("_room_bridge_fixture", _fixture_path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
setup_room = _fixture.setup_room


@pytest.mark.parametrize("runtime", ["codex-cli", "pi-cli"])
@pytest.mark.parametrize("outcome", ["ok", "failed", "timeout", "cancel"])
async def test_room_measurement_reaches_ledger_and_budget(
    db, engine, setup_room, monkeypatch, runtime, outcome
):
    db.add(Agent(id="agent-id", name="agent", engine=runtime))
    db.add(Project(id="project", name="project"))
    await db.flush()
    db.add(Room(id="room", project_id="project", name="room"))
    await db.commit()
    factory = build_session_factory(engine)
    monkeypatch.setenv("TEST_OUTCOME", outcome)
    if outcome == "timeout":
        monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "0.25")
    client = await _fixture.client_for(runtime, monkeypatch)
    try:
        task = asyncio.create_task(client._message_handlers[0](_fixture.message()))
        if outcome == "cancel":
            manager = client._execution_adapter._manager
            async with asyncio.timeout(3):
                while not manager._store.db.execute(
                    "SELECT 1 FROM events WHERE kind='progress'"
                ).fetchone():
                    await asyncio.sleep(0.01)
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            assert outcome == "cancel"
        for call in client.sendLifecycle.call_args_list:
            frame = LifecycleFrame(
                room_id=call.args[0], request_id=call.args[1], **call.kwargs
            )
            if _frame_carries_usage(frame):
                await _write_lifecycle_usage_row(
                    factory, agent_id="agent-id", frame=frame
                )
        rows = (await db.execute(select(UsageLedger))).scalars().all()
        assert len(rows) == 1
        assert (rows[0].prompt_tokens, rows[0].completion_tokens) == (3, 1)
        observed = await compute_observed_tokens(
            db,
            scope_type="agent",
            scope_id="agent-id",
            window_start=datetime.now(UTC) - timedelta(hours=24),
        )
        assert observed == 4  # measured failed/cancelled work still consumed tokens
    finally:
        await client.close()
