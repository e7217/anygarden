"""Quota availability as first-class agent state (D-2, #625).

Raft-pattern: ``quota_exhausted`` + promised reset instant, surfaced through
the availability vocabulary, the admin API, and a pre-avoidance predicate
for orchestrators. Classification is conservative (unknown errors never
block routing); clearing is proof-by-success or lapse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from anygarden.agent_availability import (
    QUOTA_EXHAUSTED,
    classify_quota_error,
    clear_quota_block,
    clear_quota_if_lapsed,
    mark_quota_exhausted,
    quota_reset_from_error,
    quota_window_active,
    render_unavailable_message,
    routing_blocked,
)
from anygarden.db.models import Agent

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _Row:
    """Minimal Agent-shaped row for the pure-function tests."""

    def __init__(
        self,
        code=None,
        detail=None,
        until=None,
        since=None,
    ):
        self.unavailable_code = code
        self.unavailable_detail = detail
        self.unavailable_until = until
        self.unavailable_since = since


def test_classification_is_conservative():
    assert classify_quota_error("429 Too Many Requests")
    assert classify_quota_error("You exceeded your current quota")
    assert classify_quota_error("usage limit reached for this account")
    assert classify_quota_error("rate limit exceeded (RESOURCE_EXHAUSTED)")
    assert classify_quota_error("billing hard limit")
    assert not classify_quota_error("connection refused")
    assert not classify_quota_error("engine crashed with exit 1")
    assert not classify_quota_error(None)
    assert not classify_quota_error("")


def test_reset_extraction_variants():
    assert quota_reset_from_error(None, now=NOW) is None
    assert quota_reset_from_error("plain failure", now=NOW) is None
    iso = quota_reset_from_error(
        "quota hit; resets at 2026-09-18T13:30:00Z", now=NOW
    )
    assert iso == datetime(2026, 9, 18, 13, 30, tzinfo=UTC)
    delta = quota_reset_from_error("rate limited, retry in 30 minutes", now=NOW)
    assert delta == NOW + timedelta(minutes=30)
    hours = quota_reset_from_error("quota resets in 2 hours", now=NOW)
    assert hours == NOW + timedelta(hours=2)


def test_mark_window_and_lazy_clear():
    agent = _Row()
    mark_quota_exhausted(agent, "429 retry in 10 minutes", now=NOW)
    assert agent.unavailable_code == QUOTA_EXHAUSTED
    assert agent.unavailable_until == NOW + timedelta(minutes=10)
    assert agent.unavailable_since == NOW
    # Window binds before the instant, not after.
    assert quota_window_active(
        agent.unavailable_code, agent.unavailable_until, now=NOW + timedelta(minutes=9)
    )
    assert not quota_window_active(
        agent.unavailable_code, agent.unavailable_until, now=NOW + timedelta(minutes=11)
    )
    # Unknown bound: blocked until proof of recovery.
    unknown = _Row()
    mark_quota_exhausted(unknown, "usage limit exceeded", now=NOW)
    assert unknown.unavailable_until is None
    assert quota_window_active(unknown.unavailable_code, None, now=NOW)
    # A sharper later failure never shortens a live window.
    mark_quota_exhausted(agent, "429 retry in 1 minute", now=NOW)
    assert agent.unavailable_until == NOW + timedelta(minutes=10)
    # Lazy clear after lapse.
    assert clear_quota_if_lapsed(agent, now=NOW + timedelta(minutes=11))
    assert agent.unavailable_code is None and agent.unavailable_until is None
    assert not clear_quota_block(agent)  # already clear


def test_routing_blocked_semantics():
    fine = _Row()
    assert not routing_blocked(fine, now=NOW)
    quota = _Row(code=QUOTA_EXHAUSTED, until=NOW + timedelta(hours=1))
    assert routing_blocked(quota, now=NOW)
    assert not routing_blocked(quota, now=NOW + timedelta(hours=2))
    # Other codes always block (owned by the lifecycle scheduler).
    crashed = _Row(code="crashed")
    assert routing_blocked(crashed, now=NOW + timedelta(days=365))


def test_message_rendering_includes_reset():
    plain = render_unavailable_message(
        QUOTA_EXHAUSTED, {"stderr_tail": "x"}, audience="user"
    )
    assert "사용량 한도" in plain
    bounded = render_unavailable_message(
        QUOTA_EXHAUSTED, {"until": "2026-09-18T13:00:00Z"}, audience="user"
    )
    assert "복구 예정" in bounded
    admin = render_unavailable_message(
        QUOTA_EXHAUSTED, {"stderr_tail": "429"}, audience="admin"
    )
    assert "429" in admin


@pytest.mark.asyncio
async def test_lifecycle_frame_marks_and_clears_agent(db):
    """Wire-level: a failed quota frame marks; a later ok frame clears."""
    from uuid import uuid4

    from anygarden.ws.handler import _apply_quota_availability
    from anygarden.ws.protocol import LifecycleFrame

    agent_id, room_id = str(uuid4()), str(uuid4())
    db.add(Agent(id=agent_id, name="quota-agent", engine="pi-cli"))
    await db.commit()

    failed = LifecycleFrame(
        request_id="req-1",
        room_id=room_id,
        event="engine_call_finished",
        outcome="failed",
        engine="pi-cli",
        error="429 usage limit exceeded, retry in 15 minutes",
    )
    await _apply_quota_availability(db, agent_id=agent_id, frame=failed)
    await db.commit()
    db.expire_all()
    stored = await db.get(Agent, agent_id)
    assert stored.unavailable_code == QUOTA_EXHAUSTED
    assert stored.unavailable_until is not None
    assert routing_blocked(stored, now=datetime.now(UTC))

    ok = LifecycleFrame(
        request_id="req-2",
        room_id=room_id,
        event="engine_call_finished",
        outcome="ok",
        engine="pi-cli",
    )
    await _apply_quota_availability(db, agent_id=agent_id, frame=ok)
    await db.commit()
    db.expire_all()
    stored = await db.get(Agent, agent_id)
    assert stored.unavailable_code is None
    assert stored.unavailable_until is None


@pytest.mark.asyncio
async def test_scheduler_codes_are_never_overwritten(db):
    from uuid import uuid4

    from anygarden.ws.handler import _apply_quota_availability
    from anygarden.ws.protocol import LifecycleFrame

    agent_id, room_id = str(uuid4()), str(uuid4())
    db.add(
        Agent(
            id=agent_id,
            name="crashed-agent",
            engine="pi-cli",
            unavailable_code="crashed",
            unavailable_detail={"exit_code": 1},
        )
    )
    await db.commit()

    failed = LifecycleFrame(
        request_id="req-3",
        room_id=room_id,
        event="handler_finished",
        outcome="failed",
        engine="pi-cli",
        error="429 rate limit",
    )
    await _apply_quota_availability(db, agent_id=agent_id, frame=failed)
    await db.commit()
    db.expire_all()
    stored = await db.get(Agent, agent_id)
    assert stored.unavailable_code == "crashed"  # untouched
