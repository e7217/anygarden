"""Delegate command — forward tasks from main room to sub-rooms.

Usage in chat:
    @에이전트 /delegate 서브룸이름 작업내용

The first correlated terminal response from a target agent completes the task.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
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
    """Forward without blocking the parent handler while the target works."""
    room_id = msg.get("room_id", "")
    request_id = (msg.get("metadata") or {}).get("request_id")
    parent_metadata = {
        key: value for key, value in (msg.get("metadata") or {}).items()
        if key in {"request_id", "turn_attempt", "turn_generation", "turn_lease", "turn_protocol", "turn_idempotency_key"}
    }
    proof = {key: value for key, value in parent_metadata.items()
             if key in {"turn_attempt", "turn_generation", "turn_lease"}}
    thread_root_id = msg.get("root_message_id")
    delegation_id = str(uuid.uuid4())
    upstream_id = (msg.get("metadata") or {}).get("delegation_id") or delegation_id
    await client.sendLifecycle(room_id, request_id, event="handler_started", **proof)

    async def failed(content: str) -> None:
        metadata = {**parent_metadata, "delegation_id": upstream_id, "delegation_outcome": "failed"}
        await client.send(
            room_id, content, metadata=metadata, thread_root_id=thread_root_id,
        )
        await client.sendLifecycle(room_id, request_id, event="handler_finished", outcome="failed", **proof)

    cancel: Callable[[], None] | None = None
    try:
        sub_room_id = await client.find_sub_room(room_id, delegate.sub_room_name)
        if not sub_room_id:
            await failed(f"서브룸 '{delegate.sub_room_name}' 를 찾을 수 없습니다")
            return
        participants = await client.get_room_participants(sub_room_id)
        candidates = [
            p for p in participants
            if p.get("kind") == "agent" and p.get("id")
            and p["id"] not in client._my_participant_ids
            and (not client._agent_id or p.get("agent_id") != client._agent_id)
            and p.get("role") in {"member", "admin", "owner"}
        ]
        if not candidates:
            await failed(f"서브룸 '{delegate.sub_room_name}' 에 작업을 받을 에이전트가 없습니다")
            return
        target = min(candidates, key=lambda p: (not p.get("online", False), p["id"]))["id"]
        if sub_room_id not in client._tasks:
            await client.join_room(sub_room_id)
        await client.wait_for_room(sub_room_id)

        # Subscribe before sending: a fast agent can reply during send().
        cancel = _register_reply_callback(
            client, room_id, sub_room_id, delegate.sub_room_name,
            delegation_id=delegation_id, target_participant_ids={target},
            parent_request_id=request_id, thread_root_id=thread_root_id,
            parent_metadata=parent_metadata, upstream_delegation_id=upstream_id,
        )
        await client.send(
            sub_room_id, f"[DELEGATED] <@user:{target}> {delegate.task}",
            metadata={"delegation_id": delegation_id, "delegation_target_participant_id": target},
        )
    except asyncio.CancelledError:
        if cancel:
            cancel()
        raise
    except Exception:
        if cancel:
            cancel()
        logger.exception("delegate.forward_failed", sub_room=delegate.sub_room_name)
        await failed(f"서브룸 '{delegate.sub_room_name}' 에 작업을 전달하지 못했습니다. 연결 상태를 확인해 주세요.")
        return
    await client.send(
        room_id, f"서브룸 '{delegate.sub_room_name}' 에 작업을 전달했습니다",
        thread_root_id=thread_root_id,
    )


def _register_reply_callback(
    client: ChatClient,
    parent_room_id: str,
    sub_room_id: str,
    sub_room_name: str,
    *,
    delegation_id: str,
    target_participant_ids: set[str],
    parent_request_id: str | None = None,
    parent_metadata: dict[str, Any] | None = None,
    upstream_delegation_id: str | None = None,
    thread_root_id: str | None = None,
    timeout: float = 300,
) -> Callable[[], None]:
    """Collect one matching agent result; return cleanup for failed sends."""
    fired = False
    timer: asyncio.Task | None = None
    parent_metadata = dict(parent_metadata or {})
    proof = {key: value for key, value in parent_metadata.items()
             if key in {"turn_attempt", "turn_generation", "turn_lease"}}
    pending: tuple[str, str] | None = None
    delivering = False
    failures = 0

    def cancel() -> None:
        nonlocal fired
        fired = True
        try:
            client._delegate_result_handlers.remove(_on_reply)
        except ValueError:
            pass
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        client._delegation_cleanups.discard(cancel)

    async def report(content: str, outcome: str) -> None:
        metadata = {**parent_metadata, "delegation_id": upstream_delegation_id or delegation_id, "delegation_outcome": outcome}
        if parent_request_id:
            metadata["request_id"] = parent_request_id
        await client.send(
            parent_room_id, content, metadata=metadata, thread_root_id=thread_root_id,
        )
        await client.sendLifecycle(
            parent_room_id, parent_request_id, event="handler_finished", outcome=outcome,
            **proof,
        )

    async def deliver() -> None:
        nonlocal delivering, failures
        if fired or delivering or pending is None:
            return
        if proof.get("turn_lease") and any(
            client._turn_context.get(parent_request_id, {}).get(key) != value
            for key, value in proof.items()
        ):
            # A retry owns a new lease. A late child must never finish it.
            cancel()
            return
        delivering = True
        try:
            await report(*pending)
            cancel()
        except Exception:  # noqa: BLE001 — retain the result after transport failure
            failures += 1
            logger.warning("delegate.result_delivery_failed", attempts=failures, sub_room=sub_room_name)
            if failures >= 3:
                cancel()
        finally:
            delivering = False

    async def _on_reply(msg: dict[str, Any]) -> None:
        nonlocal pending
        metadata = msg.get("metadata") or {}
        outcome = metadata.get("delegation_outcome")
        if (
            fired or pending is not None or msg.get("room_id") != sub_room_id
            or msg.get("participant_id") not in target_participant_ids
            or msg.get("participant_id") in client._my_participant_ids
            or metadata.get("delegation_id") != delegation_id
            or outcome not in {"ok", "failed", "timeout", "cancelled", "rejected", "retry_exhausted"}
        ):
            return
        content = msg.get("content", "")
        label = {"ok": "결과", "cancelled": "작업 취소", "timeout": "시간 초과"}.get(outcome, "작업 실패")
        pending = (f"서브룸 '{sub_room_name}' {label}:\n{content}", outcome)
        await deliver()

    client._delegate_result_handlers.append(_on_reply)
    client._delegation_cleanups.add(cancel)

    async def _cleanup() -> None:
        nonlocal pending
        deadline = asyncio.get_running_loop().time() + timeout
        while not fired:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0 and pending is None:
                pending = (f"서브룸 '{sub_room_name}' 의 위임 작업 응답 시간이 초과되었습니다", "timeout")
            if pending is not None:
                await deliver()
            if not fired:
                await asyncio.sleep(min(1, remaining) if remaining > 0 else 1)

    timer = asyncio.get_running_loop().create_task(_cleanup())
    return cancel
