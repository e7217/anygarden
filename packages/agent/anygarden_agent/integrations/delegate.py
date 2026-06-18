"""Delegate command — forward tasks from main room to sub-rooms.

Usage in chat:
    @에이전트 /delegate 서브룸이름 작업내용

v1: single response = completion (first reply from sub-room is the result).
v2 (future): sub-room agent sends ``/done result`` to signal completion.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import structlog

from anygarden_agent.client import ChatClient

logger = structlog.get_logger(__name__)

_DELEGATE_RE = re.compile(
    r"^/delegate\s+(\S+)\s+(.+)", re.DOTALL
)


@dataclass
class DelegateRequest:
    sub_room_name: str
    task: str


def parse_delegate(content: str) -> DelegateRequest | None:
    """Parse ``/delegate sub_room_name task`` from message content.

    Returns None if the content doesn't match the delegate command.
    Finds /delegate anywhere in the message — handles @mentions with
    spaces in agent names (e.g. "@테스트 에이전트 /delegate ...").
    """
    # Find /delegate anywhere in the content (after any @mention prefix)
    idx = content.find("/delegate ")
    if idx < 0:
        return None
    after = content[idx:]
    m = _DELEGATE_RE.match(after)
    if not m:
        return None
    return DelegateRequest(sub_room_name=m.group(1), task=m.group(2).strip())


async def execute_delegate(
    client: ChatClient,
    msg: dict[str, Any],
    delegate: DelegateRequest,
) -> None:
    """Execute the delegate workflow: forward task to sub-room.

    Non-blocking: sends the task and returns immediately.  A background
    callback captures the first sub-room reply and posts it back to
    the parent room automatically.  The adapter's ``_handle`` function
    is NOT blocked, so the agent can keep processing other messages
    in the main room while the sub-room works.

    Flow:
    1. Find the named sub-room under the current (parent) room.
    2. Post confirmation to the parent room.
    3. Send the task to the sub-room with [DELEGATED] prefix.
    4. Register a one-shot callback that fires when the sub-room
       agent replies → posts the result back to the parent room.
    """
    room_id = msg.get("room_id", "")

    # 1. Find sub-room
    sub_room_id = await client.find_sub_room(room_id, delegate.sub_room_name)
    if not sub_room_id:
        await client.send(
            room_id,
            f"서브룸 '{delegate.sub_room_name}' 를 찾을 수 없습니다",
        )
        return

    # Ensure we're connected to the sub-room
    if sub_room_id not in client._tasks:
        await client.join_room(sub_room_id)
        await asyncio.sleep(1)

    # 2. Confirm in parent room (immediate)
    await client.send(
        room_id,
        f"서브룸 '{delegate.sub_room_name}' 에 작업을 전달했습니다",
    )

    # 3. Send task to sub-room
    await client.send(sub_room_id, f"[DELEGATED] {delegate.task}")

    # 4. Register async callback — does NOT block _handle
    _register_reply_callback(
        client,
        parent_room_id=room_id,
        sub_room_id=sub_room_id,
        sub_room_name=delegate.sub_room_name,
    )


def _register_reply_callback(
    client: ChatClient,
    parent_room_id: str,
    sub_room_id: str,
    sub_room_name: str,
) -> None:
    """Register a one-shot message handler that captures the first
    sub-room reply and reports it back to the parent room.

    The handler auto-removes itself after firing once or after a
    timeout (5 minutes).  This is fire-and-forget — the caller
    does not await anything.
    """
    my_pids = client._my_participant_ids
    fired = False

    async def _on_reply(msg: dict[str, Any]) -> None:
        nonlocal fired
        if fired:
            return
        if msg.get("room_id") != sub_room_id:
            return
        sender = msg.get("participant_id")
        if sender and sender in my_pids:
            return  # skip own messages
        fired = True
        content = msg.get("content", "")
        logger.info(
            "delegate.reply_captured",
            sub_room=sub_room_name,
            content_len=len(content),
        )
        await client.send(
            parent_room_id,
            f"서브룸 '{sub_room_name}' 결과:\n{content}",
        )
        # Clean up: remove this handler
        try:
            client._message_handlers.remove(_on_reply)
        except ValueError:
            pass

    client._message_handlers.append(_on_reply)

    # Safety timeout: remove the handler after 5 minutes if no reply
    async def _cleanup() -> None:
        await asyncio.sleep(300)
        if not fired:
            logger.warning(
                "delegate.reply_timeout",
                sub_room=sub_room_name,
            )
            await client.send(
                parent_room_id,
                f"서브룸 '{sub_room_name}' 에서 5분 내 응답이 없습니다",
            )
            try:
                client._message_handlers.remove(_on_reply)
            except ValueError:
                pass

    asyncio.get_running_loop().create_task(_cleanup())
