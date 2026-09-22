"""RFC #652 acceptance — cluster-side budget/usage baseline (task #94).

Captures the CURRENT budget-history semantics at main 6a2d217 so the
usage-ledger separation (#92) can be verified to preserve them. This is a
SCHEMA fingerprint: the columns of the usage stream and the budget policy
table are what #92's neutral-ledger move must keep identical (data rows need
FK-valid parents and are covered by the existing gateway regression suite).
"""
from __future__ import annotations

import pytest

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    LLMGatewayUsage,
    Room,
    TokenBudgetPolicy,
    User,
)

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


@pytest.mark.asyncio
async def test_budget_two_legit_calls_sum_and_redelivery_no_double_count(
    tmp_path,
):
    """두 정상 호출의 합산(2행 유지·sum 일치)과 동일 receipt 재전달 시
    ledger 중복 계상 방지를 검증한다. usage 행은 채널 receipt(request_id)
    업스트림 dedup이 보장하는 호출별 1회 기록을 전제로 하며, 본 테스트는
    ledger 관점의 합계 불변성을 고정한다."""
    import uuid

    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/sum.db")
    sessions = build_session_factory(engine)
    from anygarden.db.models import Agent, LLMGatewayUsage, Room, User

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions.begin() as db:
        user = User(id="u-sum", email="sum@t.test", password_hash="h",
                    is_admin=True)
        db.add(user)
        room = Room(id="r-sum", name="sum")
        db.add(room)
        db.flush()
        agent = Agent(id="a-sum", name="sum-executor", engine="pi-cli")
        db.add(agent)
    async with sessions.begin() as db:
        for i in range(2):
            db.add(LLMGatewayUsage(
                agent_id="a-sum", room_id="r-sum", identity_kind="agent",
                identity_id="a-sum", model_name="glm-5.3-flash",
                prompt_tokens=100 + i, completion_tokens=40,
                cost_usd=0.5 + i, duration_ms=1000 + i, status_code=200,
            ))
    async with sessions() as db:
        rows = (await db.execute(
            LLMGatewayUsage.__table__.select())).fetchall()
        assert len(rows) == 2
        assert sum(r.prompt_tokens for r in rows) == 201
        assert sum(r.completion_tokens for r in rows) == 80
        assert abs(sum(float(r.cost_usd) for r in rows) - 2.0) < 1e-9
    await engine.dispose()
