"""WS notifications for campaign changes (#930 Phase 7).

The campaign hooks run inside other code's transactions (an execution's
terminal event, the sweeper, a machine's evaluation result, a room
archive), so they cannot broadcast themselves: the change may still roll
back. Instead they *mark* what changed on the session:

- ``campaign.updated`` for a campaign whose row changed,
- ``campaign.attempt_updated`` for an attempt that changed,
- ``task.updated`` for a cycle Task the campaign failed (the same
  ``fanout_task_event`` the sweeper and Task API use),
- a new room message (the goal's status notice, #957).

What happens to the marks after a commit:

- a caller that owns the session (REST routes, MCP tools) takes them
  before committing (:func:`take`) and publishes right after
  (:func:`publish`) on its own connection manager;
- anything left at commit time is published in the background by the
  publisher the campaign scheduler installs at start-up
  (:func:`install_publisher`), in a fresh session.

A rollback drops the marks.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from anygarden.ws.manager import ConnectionManager

log = logging.getLogger(__name__)

_KEY = "campaign_ws_notify"
_publisher: tuple[async_sessionmaker[AsyncSession], Any] | None = None
_background: set[asyncio.Task[Any]] = set()


@dataclass
class Pending:
    campaigns: set[str] = field(default_factory=set)
    attempts: set[str] = field(default_factory=set)
    tasks: set[str] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.campaigns or self.attempts or self.tasks or self.messages)


def mark(
    db: AsyncSession,
    *,
    campaign_id: str | None = None,
    attempt_id: str | None = None,
    task_id: str | None = None,
    message_id: str | None = None,
) -> None:
    pending: Pending = db.sync_session.info.setdefault(_KEY, Pending())
    if campaign_id:
        pending.campaigns.add(campaign_id)
    if attempt_id:
        pending.attempts.add(attempt_id)
    if task_id:
        pending.tasks.add(task_id)
    if message_id:
        pending.messages.add(message_id)


def take(db: AsyncSession) -> Pending:
    """Remove and return this session's marks (call before committing)."""
    return db.sync_session.info.pop(_KEY, None) or Pending()


async def publish(
    db: AsyncSession, manager: ConnectionManager | None, pending: Pending
) -> None:
    """Broadcast the committed rows *pending* names. No-op without a manager."""
    if manager is None or not pending:
        return
    from anygarden.campaigns.views import attempt_view, campaign_view
    from anygarden.db.models import Campaign, CampaignAttempt, Message, Room, Task
    from anygarden.messages.serialization import message_to_frame
    from anygarden.messages.service import fanout_task_event
    from anygarden.ws.protocol import CampaignAttemptUpdatedOut, CampaignUpdatedOut

    rooms: dict[str, str] = {}
    for attempt_id in sorted(pending.attempts):
        attempt = await db.get(CampaignAttempt, attempt_id, populate_existing=True)
        if attempt is None:
            continue
        pending.campaigns.add(attempt.campaign_id)
        campaign = await db.get(Campaign, attempt.campaign_id)
        if campaign is None:
            continue
        rooms[campaign.id] = campaign.room_id
        await manager.broadcast(campaign.room_id, CampaignAttemptUpdatedOut(
            campaign_id=campaign.id, attempt=attempt_view(attempt),
        ))
    for campaign_id in sorted(pending.campaigns):
        campaign = await db.get(Campaign, campaign_id, populate_existing=True)
        if campaign is None:
            continue
        await manager.broadcast(
            campaign.room_id, CampaignUpdatedOut(campaign=await campaign_view(db, campaign))
        )
    for task_id in sorted(pending.tasks):
        task = await db.get(Task, task_id, populate_existing=True)
        if task is None:
            continue
        room = await db.get(Room, task.room_id)
        await fanout_task_event(
            db, manager=manager, event="updated", task=task,
            room_name=room.name if room is not None else "",
        )
    for message_id in sorted(pending.messages):
        message = await db.get(Message, message_id)
        if message is not None:
            await manager.broadcast(message.room_id, message_to_frame(message))


async def commit_and_publish(db: AsyncSession, manager: ConnectionManager | None) -> None:
    """Commit *db*, then broadcast what the transaction changed."""
    pending = take(db)
    await db.commit()
    try:
        await publish(db, manager, pending)
    except Exception:  # pragma: no cover — a broadcast never fails the request
        log.exception("campaign_ws_publish_failed")


def install_publisher(
    session_factory: async_sessionmaker[AsyncSession], manager: ConnectionManager | None
) -> None:
    global _publisher
    _publisher = (session_factory, manager) if manager is not None else None


def uninstall_publisher() -> None:
    global _publisher
    _publisher = None


async def _publish_in_background(
    session_factory: async_sessionmaker[AsyncSession], manager: Any, pending: Pending
) -> None:
    try:
        async with session_factory() as db:
            await publish(db, manager, pending)
    except Exception:  # pragma: no cover — defensive
        log.exception("campaign_ws_publish_failed")


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    if session.in_nested_transaction():
        # A SAVEPOINT (``begin_nested``) committed; the outer transaction
        # has not. ``after_commit`` fires for both.
        return
    pending = session.info.pop(_KEY, None)
    if not pending or _publisher is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — sync use
        return
    session_factory, manager = _publisher
    task = loop.create_task(_publish_in_background(session_factory, manager, pending))
    _background.add(task)
    task.add_done_callback(_background.discard)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    if session.in_nested_transaction():  # a SAVEPOINT rolled back; the outer one goes on
        return
    session.info.pop(_KEY, None)


__all__ = [
    "Pending",
    "commit_and_publish",
    "install_publisher",
    "mark",
    "publish",
    "take",
    "uninstall_publisher",
]
