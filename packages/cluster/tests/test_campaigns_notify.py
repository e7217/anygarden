"""WS notifications of campaign changes made inside other transactions
(#930: ``campaigns.notify``).

A cycle Task the campaign fails (stop, sweeper, unknown close) must reach
the room as ``task.updated`` like any other Task change, and the campaign
and attempt rows as ``campaign.updated`` / ``campaign.attempt_updated``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from anygarden.campaigns import lifecycle, notify
from anygarden.db.models import Campaign, Message

from .test_campaigns_cycles import T0, env  # noqa: F401 — env is a fixture


class FakeManager:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def broadcast(self, room_id, frame, **_kwargs) -> None:
        self.frames.append((room_id, frame.model_dump()))

    async def push_to_users(self, user_ids, frame) -> None:
        pass


class TailoredManager(FakeManager):
    """Records the frames each room subscriber would receive (#969)."""

    def __init__(self, subscribers: list[str]) -> None:
        super().__init__()
        self.received: dict[str, list[dict]] = {pid: [] for pid in subscribers}

    async def broadcast(self, room_id, frame, **kwargs) -> None:
        await super().broadcast(room_id, frame, **kwargs)
        for frames in self.received.values():
            frames.append(frame.model_dump())

    async def broadcast_tailored(self, room_id, make_frame) -> set[str]:
        delivered = set()
        for pid, frames in self.received.items():
            frame = make_frame(pid)
            if frame is not None:
                frames.append(frame.model_dump())
                delivered.add(pid)
        return delivered

    def goal_messages(self, pid: str) -> list[dict]:
        return [f for f in self.received[pid]
                if f["type"] == "message" and f["metadata"].get("goal_status")]


async def _drain() -> None:
    for _ in range(20):
        if not notify._background:
            return
        await asyncio.gather(*list(notify._background))


async def test_a_task_failed_by_the_campaign_is_fanned_out(env):  # noqa: F811
    manager = FakeManager()
    notify.install_publisher(env.factory, manager)
    try:
        cid = await env.create()
        task_id = (await env.tick(T0))[0]
        await _drain()
        manager.frames.clear()
        async with env.factory() as db:
            campaign = await db.get(Campaign, cid)
            await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
            await db.commit()
        await _drain()
    finally:
        notify.uninstall_publisher()
    types = [frame["type"] for _, frame in manager.frames]
    assert {"task.updated", "campaign.updated", "campaign.attempt_updated"} <= set(types)
    task_frame = next(f for _, f in manager.frames if f["type"] == "task.updated")
    assert task_frame["task"]["id"] == task_id and task_frame["task"]["status"] == "failed"
    campaign_frame = next(f for _, f in manager.frames if f["type"] == "campaign.updated")
    assert campaign_frame["campaign"]["status"] == "stopped"
    # #957 — the room's chat hears about it too.
    notice = next(f for _, f in manager.frames if f["type"] == "message")
    assert notice["metadata"]["goal_status"]["status"] == "stopped"
    assert all(room == env.ids["room"] for room, _ in manager.frames)


async def test_a_rolled_back_change_is_not_published(env):  # noqa: F811
    manager = FakeManager()
    notify.install_publisher(env.factory, manager)
    try:
        cid = await env.create()
        await env.tick(T0)
        await _drain()
        manager.frames.clear()
        async with env.factory() as db:
            campaign = await db.get(Campaign, cid)
            await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
            await db.rollback()
        await _drain()
    finally:
        notify.uninstall_publisher()
    assert manager.frames == []


async def test_without_a_publisher_nothing_is_sent(env):  # noqa: F811
    cid = await env.create()
    await env.tick(T0 + timedelta(seconds=1))
    assert (await env.campaign(cid)).cycles_run == 1


@pytest.mark.req("CMP-17")
async def test_the_lead_gets_its_report_request_only_through_its_turn(env):  # noqa: F811
    """#969 — the report request opens the lead's turn on the message; a
    room broadcast to the lead as well made it answer the mention once
    more, untracked, and the report landed twice."""
    manager = TailoredManager([env.ids["agent_p"], "person"])
    notify.install_publisher(env.factory, manager)
    try:
        await env.create(target=0.9)
        await env.run_cycle(T0, score=0.95)
        await _drain()
    finally:
        notify.uninstall_publisher()
    [request] = manager.goal_messages("person")
    assert request["metadata"]["system_origin"] == "goal_report"
    assert manager.goal_messages(env.ids["agent_p"]) == []


@pytest.mark.req("CMP-16")
async def test_a_notice_that_opens_no_turn_still_reaches_the_lead(env):  # noqa: F811
    manager = TailoredManager([env.ids["agent_p"], "person"])
    notify.install_publisher(env.factory, manager)
    try:
        cid = await env.create()
        await env.tick(T0)
        async with env.factory() as db:
            campaign = await db.get(Campaign, cid)
            await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
            await db.commit()
        await _drain()
    finally:
        notify.uninstall_publisher()
    for pid in (env.ids["agent_p"], "person"):
        [notice] = manager.goal_messages(pid)
        assert notice["metadata"]["goal_status"]["status"] == "stopped"


@pytest.mark.req("CMP-16")
async def test_a_stop_notice_carries_no_reason_code(env):  # noqa: F811
    """#969 — ``stop_reason`` is a code ("user", "room_archived"); it showed
    up as "Goal … was stopped. user" and as the card's detail line."""
    cid = await env.create()
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
        await db.commit()
    async with env.factory() as db:
        [notice] = [m for m in await db.scalars(select(Message).where(
            Message.room_id == env.ids["room"])) if (m.extra_metadata or {}).get("goal_status")]
        campaign = await db.get(Campaign, cid)
    assert notice.content == "Goal “Win the contest” was stopped."
    assert notice.extra_metadata["goal_status"]["detail"] is None
    assert campaign.stop_reason == "user"


@pytest.mark.req("CMP-16")
async def test_a_stop_with_a_written_reason_shows_it(env):  # noqa: F811
    cid = await env.create()
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user",
                                      reason="The contest was cancelled.")
        await db.commit()
    async with env.factory() as db:
        [notice] = [m for m in await db.scalars(select(Message).where(
            Message.room_id == env.ids["room"])) if (m.extra_metadata or {}).get("goal_status")]
    assert notice.content == "Goal “Win the contest” was stopped. The contest was cancelled."
    assert notice.extra_metadata["goal_status"]["detail"] == "The contest was cancelled."
