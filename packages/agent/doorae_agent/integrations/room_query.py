"""Room query — collect opinions from agents in a target room.

When a user mentions #room and the room has a representative agent,
the server attaches ``room_query`` metadata. The representative agent
receives this, forwards the question to the target room, collects
responses from all agents, and delivers a synthesized summary back
to the source room.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

import structlog

from doorae_agent.client import ChatClient

logger = structlog.get_logger(__name__)

COLLECT_TIMEOUT = 300  # 5 minutes

# Strip ``<#room:<id>>`` tokens from forwarded content. Without this
# the server's ``parse_mentions`` would re-detect the room mention
# on the forwarded ``[ROOM_QUERY] ...`` message and re-attach
# ``room_query`` metadata, which the representative of the target
# room would then process — kicking off an infinite forwarding loop
# (each iteration prepending another ``[ROOM_QUERY]`` to the
# content). The forward should carry the *question*, not the
# *routing token* that triggered it.
_ROOM_MENTION_TOKEN = re.compile(r"<#room:[^>]+>\s*")


@dataclass
class RoomQuery:
    target_room_id: str
    source_room_id: str
    content: str
    # Issue #55: pair the question with its eventual result so the
    # source-room banner can transition pending → completed/timeout/
    # solo without a new WS event type. ``query_id`` is empty for
    # legacy in-flight messages from before the upgrade landed.
    query_id: str = ""
    # The original human author's participant_id in the source room,
    # propagated so the target-room forward can render the
    # ``↪ #room · @user`` badge. ``None`` for legacy messages.
    source_participant_id: str | None = None


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
        query_id=rq.get("query_id", ""),
        source_participant_id=rq.get("source_participant_id"),
    )


def _strip_room_mention(content: str) -> str:
    """Remove every ``<#room:...>`` token from *content* and tidy up
    the whitespace that gets left behind. Public for tests."""
    cleaned = _ROOM_MENTION_TOKEN.sub("", content)
    # Collapse the double-spaces a stripped token sometimes leaves.
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


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
        # Issue #55: pre-#55 the representative just logged and
        # returned, leaving the source-room banner pending forever.
        # We now emit a result message tagged ``status="solo"`` so
        # the chip closes and the result card can render an
        # explanatory header instead of a missing-response count.
        logger.info("room_query.solo", target=query.target_room_id)
        await _deliver_result(
            client,
            source_room_id=query.source_room_id,
            target_room_id=query.target_room_id,
            query_id=query.query_id,
            question=query.content,
            responses=[],
            expected_count=0,
            status="solo",
        )
        return

    # Send question to target room. Strip the routing token so the
    # forward carries the user's question, not the ``#room`` mention
    # that triggered the original routing — see the comment on
    # ``_ROOM_MENTION_TOKEN`` for the loop this prevents.
    forwarded = _strip_room_mention(query.content) or query.content
    await client.send(
        query.target_room_id,
        f"[ROOM_QUERY] {forwarded}",
        metadata={
            "room_query_forward": {
                "source_room_id": query.source_room_id,
                "source_participant_id": query.source_participant_id,
                "query_id": query.query_id,
            }
        },
    )

    # Register multi-reply callback
    _register_multi_reply_callback(
        client,
        source_room_id=query.source_room_id,
        target_room_id=query.target_room_id,
        query_id=query.query_id,
        expected_count=expected_count,
        question=query.content,
    )


async def _deliver_result(
    client: ChatClient,
    *,
    source_room_id: str,
    target_room_id: str,
    query_id: str,
    question: str,
    responses: list[dict[str, str]],
    expected_count: int,
    status: str,
) -> None:
    """Build the ``[취합 결과]`` body + structured metadata and
    broadcast it back to the source room.

    Centralised so the solo / completed / timeout paths all produce
    the same on-the-wire shape — only the ``status`` tag and
    ``responses`` payload differ. The body prefix stays
    ``[취합 결과]`` to keep ``should_respond``'s startswith path
    intact (plan §6.1)."""
    total = len(responses)
    missing = max(expected_count - total, 0)

    if status == "solo":
        header = "[취합 결과] (대상 방에 응답할 에이전트가 없음)"
        body = f"{header}\n\n질문: {question}"
    else:
        header = f"[취합 결과] ({total}/{expected_count}명 응답)"
        if missing > 0:
            header += f" — {missing}명 미응답"
        parts = [f"응답 {i}: {r['content']}" for i, r in enumerate(responses, 1)]
        summary = "\n".join(parts)
        body = f"{header}\n\n질문: {question}\n\n{summary}"

    await client.send(
        source_room_id,
        body,
        metadata={
            "room_query_result": {
                "query_id": query_id,
                "target_room_id": target_room_id,
                "responded": total,
                "expected": expected_count,
                "status": status,
                "responses": [
                    {
                        "participant_id": r["participant_id"],
                        "content": r["content"],
                    }
                    for r in responses
                ],
            }
        },
    )


def _register_multi_reply_callback(
    client: ChatClient,
    source_room_id: str,
    target_room_id: str,
    query_id: str,
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
            await _deliver_result(
                client,
                source_room_id=source_room_id,
                target_room_id=target_room_id,
                query_id=query_id,
                question=question,
                responses=responses,
                expected_count=expected_count,
                status="completed",
            )
            _detach_handler()

    def _detach_handler() -> None:
        try:
            client._message_handlers.remove(_on_reply)
        except ValueError:
            pass

    client._message_handlers.append(_on_reply)

    # Safety timeout
    async def _cleanup() -> None:
        await asyncio.sleep(COLLECT_TIMEOUT)
        nonlocal done
        if not done:
            done = True
            logger.warning(
                "room_query.timeout",
                collected=len(responses),
                expected=expected_count,
            )
            await _deliver_result(
                client,
                source_room_id=source_room_id,
                target_room_id=target_room_id,
                query_id=query_id,
                question=question,
                responses=responses,
                expected_count=expected_count,
                status="timeout",
            )
            _detach_handler()

    asyncio.get_event_loop().create_task(_cleanup())
