"""Least-privilege workspace attachment state and turn fencing."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Machine,
    Message,
    Participant,
    Room,
    Task,
    WorkspaceAttachment,
    WorkspaceInvocationAudit,
)

ACTIVE_ATTACHMENT_STATES = frozenset({"machine_verified", "active", "revoking"})
OPEN_TURN_STATES = frozenset({"pending", "leased", "retrying", "completing"})
BASE_CAPABILITY = "workspace_attach_v1"
READ_ROOT_CAPABILITY = "workspace_read_root_v1"
WRITE_ROOT_CAPABILITY = "workspace_write_root_v1"
AUDIT_SIGNING_CAPABILITY = "workspace_audit_signing_v1"
_AUDIT_KEY = b"anygarden-workspace-audit-unconfigured"
_SENSITIVE_KEY = re.compile(
    r"path|prompt|content|stdout|stderr|token|secret|environment|env|diff",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def configure_audit_key(value: str) -> None:
    global _AUDIT_KEY
    if value:
        _AUDIT_KEY = hashlib.sha256(
            b"anygarden-workspace-audit-v1\0" + value.encode("utf-8")
        ).digest()


def policy_hash(*, mode: str, fingerprint: str, allowlist_hash: str) -> str:
    payload = {
        "version": 1,
        "mode": mode,
        "fingerprint": fingerprint,
        "allowlist_hash": allowlist_hash,
        "raw_path_allowed": False,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def required_capabilities(mode: str) -> frozenset[str]:
    root = WRITE_ROOT_CAPABILITY if mode == "write" else READ_ROOT_CAPABILITY
    return frozenset({BASE_CAPABILITY, root, AUDIT_SIGNING_CAPABILITY})


def sanitize_workspace_catalog(value: Any) -> list[dict[str, str]]:
    """Validate a daemon catalog and drop malformed/path-bearing entries."""

    if not isinstance(value, list):
        return []
    safe: list[dict[str, str]] = []
    allowed = {
        "workspace_id",
        "label",
        "fingerprint",
        "allowlist_hash",
        "max_mode",
        "expires_at",
    }
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != allowed:
            continue
        if any(_SENSITIVE_KEY.search(str(key)) for key in raw if key not in allowed):
            continue
        row = {key: str(raw[key]) for key in allowed}
        if not row["workspace_id"].startswith("ws_"):
            continue
        if not (1 <= len(row["label"]) <= 80):
            continue
        if (
            row["label"].startswith(("/", "~", "\\\\"))
            or _WINDOWS_ABSOLUTE.match(row["label"])
            or "/" in row["label"]
            or "\\" in row["label"]
        ):
            continue
        if row["max_mode"] not in {"read", "write"}:
            continue
        if len(row["fingerprint"]) != 64 or len(row["allowlist_hash"]) != 64:
            continue
        try:
            datetime.fromisoformat(row["expires_at"])
        except ValueError:
            continue
        safe.append(row)
    return safe


def redacted_details(details: dict[str, Any] | None) -> dict[str, Any]:
    """Allow metadata-only audit projection; strip paths, content and secrets."""

    out: dict[str, Any] = {}
    for key, value in (details or {}).items():
        if _SENSITIVE_KEY.search(key):
            continue
        if isinstance(value, str) and (
            value.startswith(("/", "~", "\\\\"))
            or _WINDOWS_ABSOLUTE.match(value)
            or value.startswith(("wsc_", "mch_", "agt_"))
        ):
            out[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list):
            out[key] = [
                (
                    "[redacted]"
                    if isinstance(item, str)
                    and (
                        item.startswith(
                            ("/", "~", "\\\\", "wsc_", "mch_", "agt_")
                        )
                        or _WINDOWS_ABSOLUTE.match(item)
                    )
                    else item
                )
                for item in value
                if isinstance(item, (str, int, float, bool)) or item is None
            ][:100]
    return out


def prompt_hmac(content: str | None) -> str | None:
    if content is None:
        return None
    return hmac.new(_AUDIT_KEY, content.encode("utf-8"), hashlib.sha256).hexdigest()


async def append_audit(
    db: AsyncSession,
    *,
    attachment: WorkspaceAttachment,
    event_type: str,
    request_id: str | None = None,
    actor_user_id: str | None = None,
    actor_participant_id: str | None = None,
    task_id: str | None = None,
    source_message_id: str | None = None,
    source_thread_root_id: str | None = None,
    prompt: str | None = None,
    outcome: str | None = None,
    changed_count: int = 0,
    details: dict[str, Any] | None = None,
) -> WorkspaceInvocationAudit:
    """Append one metadata-only row chained by a server HMAC."""

    # Serialize each attachment's chain on databases that support row locks.
    # Locking the parent also covers the first audit row, where no prior audit
    # row exists to lock yet.
    await db.execute(
        select(WorkspaceAttachment.id)
        .where(WorkspaceAttachment.id == attachment.id)
        .with_for_update()
    )
    previous = (
        await db.execute(
            select(WorkspaceInvocationAudit.row_hash)
            .where(WorkspaceInvocationAudit.attachment_id == attachment.id)
            .order_by(
                WorkspaceInvocationAudit.created_at.desc(),
                WorkspaceInvocationAudit.id.desc(),
            )
            .limit(1)
        )
    ).scalar_one_or_none() or ("0" * 64)
    audit_id = str(uuid4())
    created = _now()
    safe_details = redacted_details(details)
    content_hash = prompt_hmac(prompt)
    canonical = {
        "id": audit_id,
        "attachment_id": attachment.id,
        "epoch": attachment.epoch,
        "event_type": event_type,
        "request_id": request_id,
        "actor_user_id": actor_user_id,
        "actor_participant_id": actor_participant_id,
        "room_id": attachment.room_id,
        "task_id": task_id,
        "source_message_id": source_message_id,
        "source_thread_root_id": source_thread_root_id,
        "agent_id": attachment.agent_id,
        "machine_id": attachment.machine_id,
        "mode": attachment.mode,
        "policy_hash": attachment.policy_hash,
        "prompt_hmac": content_hash,
        "outcome": outcome,
        "changed_count": changed_count,
        "details": safe_details,
        "previous_hash": previous,
        "created_at": created.isoformat(),
    }
    row_hash = hmac.new(
        _AUDIT_KEY,
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    row = WorkspaceInvocationAudit(
        id=audit_id,
        attachment_id=attachment.id,
        epoch=attachment.epoch,
        event_type=event_type,
        request_id=request_id,
        actor_user_id=actor_user_id,
        actor_participant_id=actor_participant_id,
        room_id=attachment.room_id,
        task_id=task_id,
        source_message_id=source_message_id,
        source_thread_root_id=source_thread_root_id,
        agent_id=attachment.agent_id,
        machine_id=attachment.machine_id,
        mode=attachment.mode,
        policy_hash=attachment.policy_hash,
        prompt_hmac=content_hash,
        outcome=outcome,
        changed_count=changed_count,
        details=safe_details,
        previous_hash=previous,
        row_hash=row_hash,
        created_at=created,
    )
    db.add(row)
    return row


async def active_attachment_for_agent(
    db: AsyncSession, agent_id: str
) -> WorkspaceAttachment | None:
    return (
        await db.execute(
            select(WorkspaceAttachment).where(
                WorkspaceAttachment.agent_id == agent_id,
                WorkspaceAttachment.state == "active",
            )
        )
    ).scalar_one_or_none()


async def bind_turn(
    db: AsyncSession,
    *,
    turn: AgentTurn,
    message: Message | None,
) -> tuple[bool, str | None]:
    """Bind a newly-created turn to the current attachment epoch."""

    if turn.agent_id is None:
        return True, None
    attachment = await active_attachment_for_agent(db, turn.agent_id)
    if attachment is None:
        return True, None
    turn.workspace_attachment_id = attachment.id
    turn.workspace_attachment_epoch = attachment.epoch
    reason = await _attachment_turn_reason(db, turn, attachment)
    source_participant = (
        await db.get(Participant, message.participant_id)
        if message is not None and message.participant_id
        else None
    )
    await append_audit(
        db,
        attachment=attachment,
        event_type="invocation_allowed" if reason is None else "invocation_denied",
        request_id=turn.request_id,
        actor_user_id=source_participant.user_id if source_participant else None,
        actor_participant_id=message.participant_id if message else None,
        task_id=turn.task_id,
        source_message_id=turn.trigger_message_id,
        source_thread_root_id=turn.thread_root_id,
        prompt=message.content if message else None,
        outcome="allowed" if reason is None else "denied",
        details={"reason": reason, "attachment_epoch": attachment.epoch},
    )
    return reason is None, reason


async def validate_turn(
    db: AsyncSession, turn: AgentTurn
) -> tuple[bool, str | None, WorkspaceAttachment | None]:
    """Re-check active attachment and epoch before every turn side effect."""

    if turn.agent_id is None:
        return True, None, None
    active = await active_attachment_for_agent(db, turn.agent_id)
    if turn.workspace_attachment_id is None:
        if active is not None:
            return False, "workspace_binding_missing", active
        return True, None, None
    attachment = await db.get(WorkspaceAttachment, turn.workspace_attachment_id)
    if attachment is None:
        return False, "workspace_attachment_missing", None
    reason = await _attachment_turn_reason(db, turn, attachment)
    return reason is None, reason, attachment


async def _attachment_turn_reason(
    db: AsyncSession,
    turn: AgentTurn,
    attachment: WorkspaceAttachment,
) -> str | None:
    now = _now()
    if attachment.state != "active":
        return "workspace_not_active"
    if attachment.expires_at <= now:
        return "workspace_expired"
    if turn.workspace_attachment_epoch != attachment.epoch:
        return "workspace_epoch_mismatch"
    room = await db.get(Room, attachment.room_id)
    participant = await db.get(Participant, attachment.target_participant_id)
    agent = await db.get(Agent, attachment.agent_id)
    if room is None or room.archived_at is not None:
        return "workspace_room_archived"
    if (
        participant is None
        or participant.room_id != attachment.room_id
        or participant.agent_id != attachment.agent_id
        or participant.role not in {"member", "admin", "owner"}
    ):
        return "workspace_participant_removed"
    if agent is None or agent.placed_on_machine_id != attachment.machine_id:
        return "workspace_machine_changed"
    if (
        turn.room_id != attachment.room_id
        or turn.agent_id != attachment.agent_id
        or turn.target_participant_id != attachment.target_participant_id
    ):
        return "workspace_scope_mismatch"
    if attachment.mode != "write":
        return None
    if turn.task_id is None:
        return "workspace_write_requires_task"
    message = (
        await db.get(Message, turn.trigger_message_id)
        if turn.trigger_message_id is not None
        else None
    )
    if message is None or message.room_id != attachment.room_id:
        return "workspace_source_message_mismatch"
    task = await db.get(Task, turn.task_id)
    if (
        task is None
        or task.room_id != attachment.room_id
        or task.assignee_participant_id != attachment.target_participant_id
        or task.source_message_id is None
        or task.source_message_id != turn.trigger_message_id
        or task.status != "in_progress"
    ):
        return "workspace_write_task_not_claimed"
    return None


async def cancel_attachment_turns(
    db: AsyncSession, *, attachment: WorkspaceAttachment, reason: str
) -> int:
    turns = list(
        (
            await db.scalars(
                select(AgentTurn).where(
                    AgentTurn.workspace_attachment_id == attachment.id,
                    AgentTurn.state.in_(OPEN_TURN_STATES),
                )
            )
        ).all()
    )
    now = _now()
    for turn in turns:
        turn.state = "cancelled"
        turn.terminal_reason = reason
        turn.completed_at = now
        attempt = (
            await db.execute(
                select(AgentTurnAttempt).where(
                    AgentTurnAttempt.turn_id == turn.request_id,
                    AgentTurnAttempt.attempt_number == turn.active_attempt,
                )
            )
        ).scalar_one_or_none()
        if attempt is not None and attempt.state not in {"completed", "cancelled"}:
            attempt.state = "cancelled"
            attempt.ended_at = now
            attempt.reason = reason
        await db.execute(
            update(AgentTurnOutbox)
            .where(
                AgentTurnOutbox.turn_id == turn.request_id,
                AgentTurnOutbox.state == "pending",
            )
            .values(state="cancelled", last_error=reason)
        )
        db.add(
            ActivityLog(
                agent_id=attachment.agent_id,
                event_type="turn_cancelled",
                request_id=turn.request_id,
                room_id=attachment.room_id,
                details={
                    "reason": reason,
                    "workspace_attachment_id": attachment.id,
                    "workspace_epoch": attachment.epoch,
                },
            )
        )
        await append_audit(
            db,
            attachment=attachment,
            event_type="invocation_cancelled",
            request_id=turn.request_id,
            task_id=turn.task_id,
            source_message_id=turn.trigger_message_id,
            source_thread_root_id=turn.thread_root_id,
            outcome="cancelled",
            details={"reason": reason},
        )
    return len(turns)


def machine_can_activate(
    *, machine: Machine, agent: Agent, mode: str, receipt_capabilities: list[str]
) -> tuple[bool, str | None]:
    registered = set(machine.control_capabilities or [])
    receipt = set(receipt_capabilities)
    required = required_capabilities(mode)
    if not required.issubset(registered) or not required.issubset(receipt):
        return False, "workspace_root_or_audit_capability_missing"
    # Phase 5 intentionally ships no external write adapter. Capability
    # strings are negotiation inputs, not proof that this server release can
    # safely activate writes, so even a future/malicious daemon advertising
    # every flag is refused until a separately reviewed server adapter lands.
    if mode == "write":
        return False, "workspace_write_adapter_unavailable"
    # v1's only accepted execution adapter is Codex workspace-write/read-only.
    # Claude/Gemini shell/yolo and Codex trusted danger-full-access cannot
    # enforce an exact host root, regardless of the semantic tier label.
    if agent.engine != "codex-cli":
        return False, "workspace_engine_unsupported"
    effective = agent.permission_level or "standard"
    if mode == "read" and effective != "restricted":
        return False, "workspace_read_requires_restricted"
    return True, None


def attachment_frame(attachment: WorkspaceAttachment) -> dict[str, str | int]:
    """Opaque desired-state payload; raw path is structurally impossible."""

    return {
        "attachment_id": attachment.id,
        "workspace_id": attachment.workspace_id,
        "epoch": attachment.epoch,
        "mode": attachment.mode,
        "room_id": attachment.room_id,
        "participant_id": attachment.target_participant_id,
        "allowlist_hash": attachment.allowlist_hash,
        "policy_hash": attachment.policy_hash,
        "expires_at": attachment.expires_at.isoformat(),
    }
