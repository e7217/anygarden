"""Machine receipts, expiry, membership and archive revocation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from anygarden.db.models import (
    Agent,
    Machine,
    Participant,
    Room,
    WorkspaceAttachment,
)
from anygarden.workspaces.service import (
    append_audit,
    cancel_attachment_turns,
    machine_can_activate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def handle_attach_receipt(
    session_factory: Any,
    *,
    machine_id: str,
    data: dict[str, Any],
    lifecycle: Any,
) -> None:
    """Apply a machine verification receipt with exact epoch fencing."""

    activate_agent: str | None = None
    async with session_factory() as db:
        row = await db.get(WorkspaceAttachment, str(data.get("attachment_id", "")))
        if row is None or row.machine_id != machine_id:
            return
        receipt_epoch = data.get("epoch")
        if row.state != "requested" or receipt_epoch != row.epoch:
            await append_audit(
                db,
                attachment=row,
                event_type="stale_machine_receipt",
                outcome="denied",
                details={"reason": "state_or_epoch_mismatch"},
            )
            await db.commit()
            return
        if (
            data.get("workspace_id") != row.workspace_id
            or data.get("agent_id") != row.agent_id
        ):
            row.state = "failed"
            row.failure_code = "machine_receipt_scope_mismatch"
            row.epoch += 1
            await append_audit(
                db,
                attachment=row,
                event_type="machine_verification_denied",
                outcome="denied",
                details={"reason": row.failure_code},
            )
            await db.commit()
            return
        if data.get("status") != "verified":
            row.state = "failed"
            row.failure_code = str(data.get("reason") or "machine_denied")[:128]
            row.epoch += 1
            await append_audit(
                db,
                attachment=row,
                event_type="machine_verification_denied",
                outcome="denied",
                details={"reason": row.failure_code},
            )
            await db.commit()
            return
        if (
            data.get("fingerprint") != row.fingerprint
            or data.get("allowlist_hash") != row.allowlist_hash
        ):
            row.state = "failed"
            row.failure_code = "machine_receipt_policy_mismatch"
            row.epoch += 1
            await append_audit(
                db,
                attachment=row,
                event_type="machine_verification_denied",
                outcome="denied",
                details={"reason": row.failure_code},
            )
            await db.commit()
            return

        machine = await db.get(Machine, machine_id)
        agent = await db.get(Agent, row.agent_id)
        if machine is None or agent is None or row.expires_at <= _now():
            row.state = "failed"
            row.failure_code = "attachment_target_unavailable"
            row.epoch += 1
            await append_audit(
                db,
                attachment=row,
                event_type="machine_verification_denied",
                outcome="denied",
                details={"reason": row.failure_code},
            )
            await db.commit()
            return

        row.state = "machine_verified"
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="machine_verified",
            outcome="verified",
            details={"daemon_reason": str(data.get("reason") or "verified")[:80]},
        )
        allowed, reason = machine_can_activate(
            machine=machine,
            agent=agent,
            mode=row.mode,
            receipt_capabilities=[str(v) for v in data.get("capabilities", [])],
        )
        if not allowed:
            row.state = "failed"
            row.failure_code = reason
            row.epoch += 1
            await append_audit(
                db,
                attachment=row,
                event_type="activation_denied",
                outcome="unsupported",
                details={"reason": reason},
            )
        else:
            row.state = "active"
            row.epoch += 1
            row.activated_at = _now()
            row.failure_code = None
            await append_audit(
                db,
                attachment=row,
                event_type="attachment_activated",
                outcome="active",
            )
            activate_agent = row.agent_id
        await db.commit()

    if activate_agent is not None:
        # The next desired-state frame carries only opaque lease metadata.
        # A daemon falsely advertising support still fails closed in Spawner.
        await lifecycle.bump_generation(activate_agent)


async def handle_revoke_receipt(
    session_factory: Any,
    *,
    machine_id: str,
    data: dict[str, Any],
    lifecycle: Any,
) -> None:
    resume_agent: str | None = None
    async with session_factory() as db:
        row = await db.get(WorkspaceAttachment, str(data.get("attachment_id", "")))
        if row is None or row.machine_id != machine_id:
            return
        if row.state != "revoking" or data.get("epoch") != row.epoch:
            await append_audit(
                db,
                attachment=row,
                event_type="stale_revoke_receipt",
                outcome="denied",
                details={"reason": "state_or_epoch_mismatch"},
            )
            await db.commit()
            return
        if data.get("status") not in {"stopped", "not_running"}:
            row.failure_code = "workspace_revoke_stop_failed"
            await append_audit(
                db,
                attachment=row,
                event_type="revoke_stop_failed",
                outcome="failed",
                details={"reason": row.failure_code},
            )
            await db.commit()
            return
        row.state = "revoked"
        row.epoch += 1
        row.revoked_at = _now()
        await append_audit(
            db,
            attachment=row,
            event_type="revoked",
            outcome="revoked",
        )
        if row.resume_after_revoke:
            resume_agent = row.agent_id
        await db.commit()
    if resume_agent is not None:
        await lifecycle.request_start(resume_agent)


async def revoke_invalid_attachments(
    session_factory: Any, machine_bus: Any, lifecycle: Any
) -> int:
    """Fence expiry/archive/removal/machine moves and stop active processes."""

    now = _now()
    revoke_frames: list[tuple[str, dict[str, Any], str]] = []
    changed = 0
    async with session_factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(WorkspaceAttachment).where(
                        WorkspaceAttachment.state.in_(
                            {"requested", "machine_verified", "active", "revoking"}
                        )
                    )
                )
            ).all()
        )
        for row in rows:
            if row.state == "revoking":
                changed += 1
                revoke_frames.append(
                    (
                        row.machine_id,
                        {
                            "type": "workspace_revoke",
                            "attachment_id": row.id,
                            "agent_id": row.agent_id,
                            "epoch": row.epoch,
                        },
                        row.agent_id,
                    )
                )
                continue
            room = await db.get(Room, row.room_id)
            participant = await db.get(Participant, row.target_participant_id)
            agent = await db.get(Agent, row.agent_id)
            reason = None
            if row.expires_at <= now:
                reason = "workspace_attachment_expired"
            elif room is None or room.archived_at is not None:
                reason = "workspace_room_archived"
            elif (
                participant is None
                or participant.room_id != row.room_id
                or participant.agent_id != row.agent_id
                or participant.role not in {"member", "admin", "owner"}
            ):
                reason = "workspace_participant_removed"
            elif agent is None or agent.placed_on_machine_id != row.machine_id:
                reason = "workspace_machine_changed"
            if reason is None:
                continue
            row.epoch += 1
            changed += 1
            was_active = row.state in {"machine_verified", "active"}
            row.state = "revoking" if was_active else "expired"
            row.failure_code = reason
            await cancel_attachment_turns(db, attachment=row, reason=reason)
            await append_audit(
                db,
                attachment=row,
                event_type="automatic_revoke" if was_active else "expired",
                outcome=row.state,
                details={"reason": reason},
            )
            if was_active:
                revoke_frames.append(
                    (
                        row.machine_id,
                        {
                            "type": "workspace_revoke",
                            "attachment_id": row.id,
                            "agent_id": row.agent_id,
                            "epoch": row.epoch,
                        },
                        row.agent_id,
                    )
                )
        if changed:
            await db.commit()

    for machine_id, frame, agent_id in revoke_frames:
        await lifecycle.request_stop(agent_id)
        await machine_bus.send(machine_id, frame)
    return changed
