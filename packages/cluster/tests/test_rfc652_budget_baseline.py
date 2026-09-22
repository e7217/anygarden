"""RFC #652 acceptance — cluster-side budget/usage baseline (task #94).

Schema fingerprint of the usage stream + policy table that the usage-ledger
separation (#92) must preserve, plus the WS-frame duplicate-delivery
behavior: the same engine_call_finished frame written twice produces TWO
rows (no idempotency key on this path) — documented, not a defect claim.
"""
from __future__ import annotations

import pytest

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import LLMGatewayUsage, TokenBudgetPolicy

USAGE_COLS = [
    "id", "timestamp", "agent_id", "room_id", "identity_kind", "identity_id",
    "model_name", "prompt_tokens", "completion_tokens", "cost_usd",
    "duration_ms", "status_code", "error",
]
POLICY_COLS = [
    "id", "scope_type", "scope_id", "token_ceiling", "warn_percent",
    "window_kind", "hard_stop_enabled", "is_active", "created_at",
    "updated_at",
]


def test_budget_usage_stream_schema_baseline():
    cols = [c.name for c in LLMGatewayUsage.__table__.columns]
    assert cols == USAGE_COLS, cols


def test_budget_policy_schema_baseline():
    cols = [c.name for c in TokenBudgetPolicy.__table__.columns]
    assert cols == POLICY_COLS, cols


async def test_budget_ws_duplicate_frame_delivery_double_counts(tmp_path):
    """The same engine_call_finished frame delivered twice through the WS
    usage writer produces TWO rows — the write path carries no idempotency
    key, so redelivery double-counts. Documented current behavior."""
    from anygarden.ws.protocol import LifecycleFrame
    from anygarden.ws.handler import _write_lifecycle_usage_row

    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/ws.db")
    sessions = build_session_factory(engine)
    from anygarden.db.models import Agent, Base, Room, User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions.begin() as db:
        db.add(User(id="u-1", email="o@t.test", password_hash="h"))
        db.add(Room(id="room-x", name="x"))
        db.add(Agent(id="agent-1", name="a1", engine="codex-cli"))

    frame = LifecycleFrame(
        request_id="req-dup-1", room_id="room-x", turn_attempt=1,
        event="engine_call_finished", outcome="ok",
        engine="codex-cli", duration_ms=500,
        input_tokens=100, output_tokens=40, model="glm-5.3-flash",
    )
    async with sessions.begin() as db:
        pass  # schema already created by create_all above

    await _write_lifecycle_usage_row(
        sessions, agent_id="agent-1", frame=frame)
    await _write_lifecycle_usage_row(
        sessions, agent_id="agent-1", frame=frame)

    async with sessions() as db:
        rows = (await db.execute(
            LLMGatewayUsage.__table__.select())).fetchall()
        assert len(rows) == 2  # no idempotency key: both deliveries count

    await engine.dispose()
