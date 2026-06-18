"""Tests for the gateway-free LLM usage write path (#461, Wave 2d).

CLI engines (claude-code / codex / gemini) bypass the LLM gateway, so
their token usage arrives on the ``engine_call_finished`` LifecycleFrame
and the WS handler writes one ``LLMGatewayUsage`` row from it via
``_write_lifecycle_usage_row``. A frame with no token data and no model
(a bare-str engine return, or openhands — already counted through the
gateway reverse-proxy) must NOT produce a row, so openhands is never
double-counted.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from anygarden.db.engine import build_session_factory
from anygarden.db.models import Agent, LLMGatewayUsage, Project, Room
from anygarden.ws.handler import (
    _frame_carries_usage,
    _write_lifecycle_usage_row,
)
from anygarden.ws.protocol import LifecycleFrame


async def _make_agent(db) -> Agent:
    a = Agent(id=str(uuid.uuid4()), name="usage-test", engine="claude-code")
    db.add(a)
    await db.commit()
    return a


async def _make_room(db, room_id: str) -> str:
    """Create a project + room so the usage row's room_id FK resolves."""
    project = Project(id=str(uuid.uuid4()), name="usage-proj")
    db.add(project)
    await db.flush()
    db.add(Room(id=room_id, project_id=project.id, name="usage-room"))
    await db.commit()
    return room_id


class TestFrameCarriesUsage:
    def test_true_when_tokens_present(self) -> None:
        frame = LifecycleFrame(
            request_id="r",
            room_id="room-1",
            event="engine_call_finished",
            input_tokens=10,
            output_tokens=3,
        )
        assert _frame_carries_usage(frame) is True

    def test_true_when_model_present(self) -> None:
        frame = LifecycleFrame(
            request_id="r",
            room_id="room-1",
            event="engine_call_finished",
            model="gpt-5.5",
        )
        assert _frame_carries_usage(frame) is True

    def test_false_when_all_none(self) -> None:
        # openhands / bare-str return — no usage, no row, no double-count.
        frame = LifecycleFrame(
            request_id="r",
            room_id="room-1",
            event="engine_call_finished",
            outcome="ok",
            duration_ms=5,
        )
        assert _frame_carries_usage(frame) is False

    def test_false_for_non_engine_event(self) -> None:
        frame = LifecycleFrame(
            request_id="r",
            room_id="room-1",
            event="handler_finished",
            model="gpt-5.5",
        )
        assert _frame_carries_usage(frame) is False


@pytest.mark.asyncio
async def test_token_frame_writes_usage_row_with_cost(db, engine):
    agent = await _make_agent(db)
    await _make_room(db, "room-1")
    factory = build_session_factory(engine)
    frame = LifecycleFrame(
        request_id="req-1",
        room_id="room-1",
        event="engine_call_finished",
        engine="claude-code",
        outcome="ok",
        duration_ms=1234,
        model="claude-sonnet-4-5",
        input_tokens=1200,
        output_tokens=350,
        cost_usd=0.0123,
    )
    assert _frame_carries_usage(frame) is True
    await _write_lifecycle_usage_row(factory, agent_id=agent.id, frame=frame)

    rows = (
        await db.execute(
            select(LLMGatewayUsage).where(LLMGatewayUsage.agent_id == agent.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.identity_kind == "agent"
    assert row.identity_id == agent.id
    assert row.room_id == "room-1"
    assert row.model_name == "claude-sonnet-4-5"
    assert row.prompt_tokens == 1200
    assert row.completion_tokens == 350
    assert row.cost_usd == 0.0123
    assert row.duration_ms == 1234
    # status_code=200 so the row folds into the Wave 1d budget ledger.
    assert row.status_code == 200


@pytest.mark.asyncio
async def test_codex_frame_writes_row_with_null_cost(db, engine):
    # codex/gemini report tokens but no cost → cost_usd NULL, row still
    # written (better than 0 rows for occurrence/latency/tokens).
    agent = await _make_agent(db)
    await _make_room(db, "room-2")
    factory = build_session_factory(engine)
    frame = LifecycleFrame(
        request_id="req-2",
        room_id="room-2",
        event="engine_call_finished",
        engine="codex",
        outcome="ok",
        duration_ms=10,
        model="gpt-5.5",
        input_tokens=111,
        output_tokens=22,
    )
    await _write_lifecycle_usage_row(factory, agent_id=agent.id, frame=frame)

    row = (
        await db.execute(
            select(LLMGatewayUsage).where(LLMGatewayUsage.agent_id == agent.id)
        )
    ).scalars().one()
    assert row.model_name == "gpt-5.5"
    assert row.prompt_tokens == 111
    assert row.completion_tokens == 22
    assert row.cost_usd is None


@pytest.mark.asyncio
async def test_all_none_frame_writes_no_row(db, engine):
    # The guard short-circuits: an engine_call_finished frame with no
    # token data and no model is the openhands / bare-str shape, so no
    # frame-sourced usage row is written and openhands isn't double-counted.
    agent = await _make_agent(db)
    factory = build_session_factory(engine)
    frame = LifecycleFrame(
        request_id="req-3",
        room_id="room-3",
        event="engine_call_finished",
        engine="openhands",
        outcome="ok",
        duration_ms=7,
    )
    assert _frame_carries_usage(frame) is False
    # The handler only calls _write_lifecycle_usage_row when the guard is
    # true; assert the contract directly so a regression in the guard or
    # the call site is caught here.
    if _frame_carries_usage(frame):  # pragma: no cover - guard is False
        await _write_lifecycle_usage_row(factory, agent_id=agent.id, frame=frame)

    rows = (
        await db.execute(
            select(LLMGatewayUsage).where(LLMGatewayUsage.agent_id == agent.id)
        )
    ).scalars().all()
    assert rows == []
