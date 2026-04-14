"""Room query — collect opinions from agents in a target room.

When a user mentions #room and the room has a representative agent,
the server attaches ``room_query`` metadata. The representative agent
receives this, forwards the question to the target room, collects
responses from all agents, and delivers a synthesized summary back
to the source room.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

from doorae_agent.client import ChatClient

logger = structlog.get_logger(__name__)

COLLECT_TIMEOUT = 300  # 5 minutes


@dataclass
class RoomQuery:
    target_room_id: str
    source_room_id: str
    content: str


def parse_room_query(msg: dict[str, Any]) -> RoomQuery | None:
    """Extract room_query from message metadata."""
    metadata = msg.get("metadata") or {}
    rq = metadata.get("room_query")
    if not rq:
        return None
    return RoomQuery(
        target_room_id=rq["target_room_id"],
        source_room_id=rq["source_room_id"],
        content=msg.get("content", ""),
    )


async def execute_room_query(
    client: ChatClient,
    msg: dict[str, Any],
    query: RoomQuery,
) -> None:
    """Execute room query: forward question, collect responses, summarize.

    Non-blocking: registers callbacks and returns immediately.
    """
    # Ensure connected to target room
    if query.target_room_id not in client._tasks:
        await client.join_room(query.target_room_id)
        await asyncio.sleep(1)

    # Get agent participants in target room (excluding self)
    participants = await client.get_room_participants(query.target_room_id)
    my_pids = client._my_participant_ids
    agent_participants = [
        p for p in participants
        if p.get("kind") == "agent" and p.get("id") not in my_pids
    ]
    expected_count = len(agent_participants)

    if expected_count == 0:
        # Representative is alone — respond directly via adapter
        logger.info("room_query.solo", target=query.target_room_id)
        return

    # Send question to target room
    await client.send(
        query.target_room_id,
        f"[ROOM_QUERY] {query.content}",
    )

    # Register multi-reply callback
    _register_multi_reply_callback(
        client,
        source_room_id=query.source_room_id,
        target_room_id=query.target_room_id,
        expected_count=expected_count,
        question=query.content,
    )


def _register_multi_reply_callback(
    client: ChatClient,
    source_room_id: str,
    target_room_id: str,
    expected_count: int,
    question: str,
) -> None:
    """Register a callback that collects N responses from agents.

    Fires synthesis when all agents respond or on timeout.
    """
    my_pids = client._my_participant_ids
    responses: list[dict[str, str]] = []
    done = False

    async def _on_reply(msg: dict[str, Any]) -> None:
        nonlocal done
        if done:
            return
        if msg.get("room_id") != target_room_id:
            return
        sender = msg.get("participant_id")
        if sender and sender in my_pids:
            return
        # Skip other [ROOM_QUERY] messages (avoid catching our own broadcast)
        content = msg.get("content", "")
        if content.startswith("[ROOM_QUERY]"):
            return

        responses.append({
            "participant_id": sender or "unknown",
            "content": content,
        })
        logger.info(
            "room_query.response_collected",
            count=len(responses),
            expected=expected_count,
        )

        if len(responses) >= expected_count:
            done = True
            await _synthesize_and_deliver()

    async def _synthesize_and_deliver() -> None:
        """Synthesize collected responses and send to source room."""
        # Build summary from collected responses
        parts = []
        for i, r in enumerate(responses, 1):
            parts.append(f"응답 {i}: {r['content']}")

        summary = "\n".join(parts)
        total = len(responses)
        missing = expected_count - total

        header = f"[취합 결과] ({total}/{expected_count}명 응답)"
        if missing > 0:
            header += f" — {missing}명 미응답"

        await client.send(
            source_room_id,
            f"{header}\n\n질문: {question}\n\n{summary}",
        )

        # Cleanup handler
        try:
            client._message_handlers.remove(_on_reply)
        except ValueError:
            pass

    client._message_handlers.append(_on_reply)

    # Safety timeout
    async def _cleanup() -> None:
        await asyncio.sleep(COLLECT_TIMEOUT)
        if not done:
            logger.warning(
                "room_query.timeout",
                collected=len(responses),
                expected=expected_count,
            )
            await _synthesize_and_deliver()

    asyncio.get_event_loop().create_task(_cleanup())
