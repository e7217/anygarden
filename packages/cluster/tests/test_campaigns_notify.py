"""WS notifications of campaign changes made inside other transactions
(#930: ``campaigns.notify``).

A cycle Task the campaign fails (stop, sweeper, unknown close) must reach
the room as ``task.updated`` like any other Task change, and the campaign
and attempt rows as ``campaign.updated`` / ``campaign.attempt_updated``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from anygarden.campaigns import lifecycle, notify
from anygarden.db.models import Campaign

from .test_campaigns_cycles import T0, env  # noqa: F401 — env is a fixture


class FakeManager:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def broadcast(self, room_id, frame, **_kwargs) -> None:
        self.frames.append((room_id, frame.model_dump()))

    async def push_to_users(self, user_ids, frame) -> None:
        pass


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
