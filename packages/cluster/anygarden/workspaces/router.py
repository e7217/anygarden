"""REST control plane for opaque workspace attachment leases."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import (
    ActivityLog,
    Agent,
    Machine,
    Participant,
    WorkspaceAttachment,
    WorkspaceInvocationAudit,
)
from anygarden.dependencies import get_admin_identity, get_current_identity, get_db
from anygarden.rooms.authorization import (
    Capability,
    is_global_admin,
    require_capability,
)
from anygarden.workspaces.service import (
    ACTIVE_ATTACHMENT_STATES,
    RECEIPT_SIGNING_CAPABILITY,
    append_audit,
    cancel_attachment_turns,
    machine_can_activate,
    normalize_workspace_signing_public_key,
    policy_hash,
    required_capabilities,
    workspace_mode_support,
)

router = APIRouter(prefix="/api/v1/rooms", tags=["workspace-attachments"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _catalog_entry(machine: Machine, workspace_id: str) -> dict[str, str] | None:
    for row in machine.workspace_catalog or []:
        if isinstance(row, dict) and row.get("workspace_id") == workspace_id:
            return {str(key): str(value) for key, value in row.items()}
    return None


class AttachmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    participant_id: str
    workspace_id: str = Field(pattern=r"^ws_[A-Za-z0-9_-]{8,120}$")
    mode: Literal["read", "write"]
    expires_in_seconds: int = Field(default=3600, ge=60, le=86400)


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_proof: SecretStr

    @field_validator("consent_proof")
    @classmethod
    def validate_consent_proof(cls, value: SecretStr) -> SecretStr:
        if not re.fullmatch(r"wcp_[0-9a-f]{64}", value.get_secret_value()):
            raise ValueError("invalid consent proof")
        return value


class AttachmentOut(BaseModel):
    id: str
    workspace_id: str
    workspace_label: str
    machine_id: str
    agent_id: str
    room_id: str
    target_participant_id: str
    mode: str
    state: str
    epoch: int
    expires_at: datetime
    policy_hash: str
    room_approved_by_user_id: str | None = None
    global_approved_by_user_id: str | None = None
    failure_code: str | None = None
    activated_at: datetime | None = None
    revoked_at: datetime | None = None
    model_config = ConfigDict(from_attributes=True)


class WorkspaceModeSupport(BaseModel):
    supported: bool
    reason: str | None = None


class WorkspaceOption(BaseModel):
    workspace_id: str
    label: str
    max_mode: Literal["read", "write"]
    expires_at: datetime


class WorkspaceOptions(BaseModel):
    agent_id: str
    participant_id: str
    machine_id: str | None
    machine_name: str | None
    execution_kind: Literal["integrated", "remote"] | None
    node_data_dir: str | None
    can_approve_room: bool
    can_approve_global: bool
    read: WorkspaceModeSupport
    write: WorkspaceModeSupport
    workspaces: list[WorkspaceOption]


class WorkspaceAuditOut(BaseModel):
    id: str
    attachment_id: str
    epoch: int
    event_type: str
    request_id: str | None
    room_id: str
    task_id: str | None
    source_message_id: str | None
    source_thread_root_id: str | None
    agent_id: str
    machine_id: str
    mode: str
    policy_hash: str
    prompt_hmac: str | None
    outcome: str | None
    changed_count: int
    details: dict | None
    previous_hash: str
    row_hash: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


async def _attachment_or_404(
    db: AsyncSession, room_id: str, attachment_id: str
) -> WorkspaceAttachment:
    row = await db.get(WorkspaceAttachment, attachment_id)
    if row is None or row.room_id != room_id:
        raise HTTPException(status_code=404, detail="Workspace attachment not found")
    return row


@router.get("/{room_id}/workspace-attachments/options", response_model=WorkspaceOptions)
async def workspace_options(
    room_id: str,
    agent_id: str,
    request: Request,
    identity: Annotated[Identity, Depends(get_current_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceOptions:
    """Room-scoped, redacted inventory and server-authoritative support limits."""
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.WORKSPACE_ATTACH_MANAGE,
    )
    participant = (
        await db.scalars(
            select(Participant).where(
                Participant.room_id == room_id,
                Participant.agent_id == agent_id,
                Participant.role.in_({"member", "admin", "owner"}),
            )
        )
    ).first()
    agent = await db.get(Agent, agent_id)
    if participant is None or agent is None:
        raise HTTPException(status_code=404, detail="Room agent participant not found")
    machine = (
        await db.get(Machine, agent.placed_on_machine_id)
        if agent.placed_on_machine_id
        else None
    )
    read_ok, read_reason = workspace_mode_support(machine, agent, "read")
    write_ok, write_reason = workspace_mode_support(machine, agent, "write")
    entries = []
    for entry in machine.workspace_catalog or [] if machine else []:
        try:
            option = WorkspaceOption.model_validate(entry)
        except ValidationError:
            continue
        if option.expires_at.replace(tzinfo=option.expires_at.tzinfo or UTC) > _now():
            entries.append(option)
    integrated = bool(
        machine and machine.id == getattr(request.app.state, "local_machine_id", None)
    )
    node_data_dir = getattr(
        getattr(request.app.state, "config", None), "local_node_data_dir", None
    )
    return WorkspaceOptions(
        agent_id=agent.id,
        participant_id=participant.id,
        machine_id=machine.id if machine else None,
        machine_name=machine.name if machine else None,
        execution_kind=("integrated" if integrated else "remote") if machine else None,
        # Only system administrators receive a local filesystem path for CLI use.
        node_data_dir=str(node_data_dir)
        if integrated and is_global_admin(identity) and node_data_dir is not None
        else None,
        can_approve_room=bool(
            access.participant and access.effective_role in {"admin", "owner"}
        ),
        can_approve_global=is_global_admin(identity),
        read=WorkspaceModeSupport(supported=read_ok, reason=read_reason),
        write=WorkspaceModeSupport(supported=write_ok, reason=write_reason),
        workspaces=entries,
    )


@router.post(
    "/{room_id}/workspace-attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=AttachmentOut,
)
async def create_attachment(
    room_id: str,
    body: AttachmentCreate,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachment:
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.WORKSPACE_ATTACH_MANAGE,
    )
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="User approval required")

    participant = await db.get(Participant, body.participant_id)
    agent = await db.get(Agent, body.agent_id)
    if (
        participant is None
        or participant.room_id != room_id
        or participant.agent_id != body.agent_id
        or participant.role not in {"member", "admin", "owner"}
    ):
        raise HTTPException(status_code=404, detail="Room agent participant not found")
    if agent is None or agent.placed_on_machine_id is None:
        raise HTTPException(status_code=409, detail="Agent is not placed on a machine")
    machine = await db.get(Machine, agent.placed_on_machine_id)
    if machine is None:
        raise HTTPException(status_code=409, detail="Agent machine not found")
    supported, reason = workspace_mode_support(machine, agent, body.mode)
    if not supported:
        raise HTTPException(
            status_code=409,
            detail={
                "code": reason,
                "message": "This agent and machine do not support the requested external workspace access.",
            },
        )
    catalog = _catalog_entry(machine, body.workspace_id)
    if catalog is None:
        raise HTTPException(
            status_code=404, detail="Workspace registration not advertised"
        )
    catalog_expiry = datetime.fromisoformat(catalog["expires_at"])
    if catalog_expiry.tzinfo is None:
        catalog_expiry = catalog_expiry.replace(tzinfo=timezone.utc)
    expires_at = min(
        _now() + timedelta(seconds=body.expires_in_seconds), catalog_expiry
    )
    if expires_at <= _now():
        raise HTTPException(status_code=409, detail="Workspace registration expired")
    if body.mode == "write" and catalog.get("max_mode") != "write":
        raise HTTPException(
            status_code=409, detail="Workspace registration is read-only"
        )
    existing = (
        await db.execute(
            select(WorkspaceAttachment.id).where(
                WorkspaceAttachment.agent_id == agent.id,
                WorkspaceAttachment.state.in_(ACTIVE_ATTACHMENT_STATES | {"requested"}),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Agent already has an attachment")

    room_approver = None
    if access.participant is not None and access.effective_role in {"admin", "owner"}:
        room_approver = identity.id
    global_approver = identity.id if is_global_admin(identity) else None
    row = WorkspaceAttachment(
        workspace_id=body.workspace_id,
        workspace_label=catalog["label"],
        machine_id=machine.id,
        agent_id=agent.id,
        room_id=room_id,
        target_participant_id=participant.id,
        mode=body.mode,
        state="requested",
        epoch=1,
        fingerprint=catalog["fingerprint"],
        allowlist_hash=catalog["allowlist_hash"],
        policy_hash=policy_hash(
            mode=body.mode,
            fingerprint=catalog["fingerprint"],
            allowlist_hash=catalog["allowlist_hash"],
        ),
        expires_at=expires_at,
        requested_by_user_id=identity.id,
        room_approved_by_user_id=room_approver,
        global_approved_by_user_id=global_approver,
    )
    db.add(row)
    await db.flush()
    await append_audit(
        db,
        attachment=row,
        event_type="attachment_requested",
        actor_user_id=identity.id,
        outcome="requested",
        details={
            "room_approved": room_approver is not None,
            "global_approved": global_approver is not None,
        },
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/{room_id}/workspace-attachments", response_model=list[AttachmentOut])
async def list_attachments(
    room_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceAttachment]:
    await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.ROOM_READ,
    )
    return list(
        (
            await db.scalars(
                select(WorkspaceAttachment)
                .where(WorkspaceAttachment.room_id == room_id)
                .order_by(WorkspaceAttachment.created_at.desc())
            )
        ).all()
    )


@router.post(
    "/{room_id}/workspace-attachments/{attachment_id}/approve-room",
    response_model=AttachmentOut,
)
async def approve_room(
    room_id: str,
    attachment_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachment:
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.WORKSPACE_ATTACH_MANAGE,
    )
    if (
        identity.kind != "user"
        or access.participant is None
        or access.effective_role not in {"admin", "owner"}
    ):
        raise HTTPException(
            status_code=403, detail="Current room admin approval required"
        )
    row = await _attachment_or_404(db, room_id, attachment_id)
    if row.state != "requested":
        raise HTTPException(
            status_code=409, detail="Attachment is not awaiting approval"
        )
    if row.room_approved_by_user_id is None:
        row.room_approved_by_user_id = identity.id
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="room_approved",
            actor_user_id=identity.id,
            outcome="approved",
        )
        await db.commit()
        await db.refresh(row)
    return row


@router.post(
    "/{room_id}/workspace-attachments/{attachment_id}/approve-global",
    response_model=AttachmentOut,
)
async def approve_global(
    room_id: str,
    attachment_id: str,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachment:
    row = await _attachment_or_404(db, room_id, attachment_id)
    if row.state != "requested":
        raise HTTPException(
            status_code=409, detail="Attachment is not awaiting approval"
        )
    if row.global_approved_by_user_id is None:
        row.global_approved_by_user_id = identity.id
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="global_approved",
            actor_user_id=identity.id,
            outcome="approved",
        )
        await db.commit()
        await db.refresh(row)
    return row


@router.post(
    "/{room_id}/workspace-attachments/{attachment_id}/verify",
    response_model=AttachmentOut,
)
async def verify_attachment(
    room_id: str,
    attachment_id: str,
    body: VerifyBody,
    request: Request,
    identity: Identity = Depends(get_admin_identity),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachment:
    row = await _attachment_or_404(db, room_id, attachment_id)
    if row.state != "requested":
        raise HTTPException(status_code=409, detail="Attachment cannot be verified")
    if row.room_approved_by_user_id is None or row.global_approved_by_user_id is None:
        raise HTTPException(status_code=409, detail="Both approvals are required")
    if row.expires_at <= _now():
        row.state = "expired"
        row.failure_code = "attachment_expired"
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="verification_denied",
            actor_user_id=identity.id,
            outcome="expired",
            details={"reason": row.failure_code},
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="Attachment expired")
    machine = await db.get(Machine, row.machine_id)
    agent = await db.get(Agent, row.agent_id)
    if machine is None or agent is None:
        raise HTTPException(status_code=409, detail="Attachment target unavailable")
    control_capabilities = set(machine.control_capabilities or [])
    enrolled_key = normalize_workspace_signing_public_key(
        machine.workspace_signing_public_key
    )
    signing_reason = None
    if enrolled_key is None:
        signing_reason = (
            "workspace_receipt_key_unenrolled"
            if machine.workspace_signing_public_key is None
            else "workspace_receipt_key_invalid"
        )
    elif RECEIPT_SIGNING_CAPABILITY not in control_capabilities:
        signing_reason = "workspace_receipt_signing_unavailable"
    if signing_reason is not None:
        row.state = "failed"
        row.failure_code = signing_reason
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="verification_denied",
            actor_user_id=identity.id,
            outcome="unsupported",
            details={"reason": signing_reason},
        )
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="Machine workspace receipt signing is unavailable",
        )
    required = required_capabilities(row.mode)
    if not required.issubset(control_capabilities):
        row.state = "failed"
        row.failure_code = "workspace_root_or_audit_capability_missing"
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="verification_denied",
            actor_user_id=identity.id,
            outcome="unsupported",
            details={"reason": row.failure_code},
        )
        await db.commit()
        raise HTTPException(
            status_code=409,
            detail="Machine lacks workspace root enforcement or audit signing",
        )
    engine_ok, engine_reason = machine_can_activate(
        machine=machine,
        agent=agent,
        mode=row.mode,
        receipt_capabilities=list(machine.control_capabilities or []),
    )
    if not engine_ok:
        row.state = "failed"
        row.failure_code = engine_reason
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="verification_denied",
            actor_user_id=identity.id,
            outcome="unsupported",
            details={"reason": engine_reason},
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="Workspace engine mode unsupported")

    row.epoch += 1
    await append_audit(
        db,
        attachment=row,
        event_type="machine_verification_requested",
        actor_user_id=identity.id,
        outcome="pending",
    )
    epoch = row.epoch
    await db.commit()
    sent = await request.app.state.machine_bus.send(
        row.machine_id,
        {
            "type": "workspace_attach_request",
            "attachment_id": row.id,
            "workspace_id": row.workspace_id,
            "agent_id": row.agent_id,
            "room_id": row.room_id,
            "participant_id": row.target_participant_id,
            "epoch": epoch,
            "mode": row.mode,
            "fingerprint": row.fingerprint,
            "allowlist_hash": row.allowlist_hash,
            "policy_hash": row.policy_hash,
            "expires_at": row.expires_at.isoformat(),
            "consent_proof": body.consent_proof.get_secret_value(),
        },
    )
    if not sent:
        row.state = "failed"
        row.failure_code = "machine_offline"
        row.epoch += 1
        await append_audit(
            db,
            attachment=row,
            event_type="verification_denied",
            actor_user_id=identity.id,
            outcome="offline",
            details={"reason": row.failure_code},
        )
        await db.commit()
        raise HTTPException(status_code=409, detail="Machine is offline")
    await db.refresh(row)
    return row


@router.delete(
    "/{room_id}/workspace-attachments/{attachment_id}",
    response_model=AttachmentOut,
)
async def revoke_attachment(
    room_id: str,
    attachment_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceAttachment:
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.WORKSPACE_ATTACH_MANAGE,
    )
    if identity.kind != "user":
        raise HTTPException(status_code=403, detail="User revocation required")
    if not is_global_admin(identity) and (
        access.participant is None or access.effective_role not in {"admin", "owner"}
    ):
        raise HTTPException(status_code=403, detail="Room admin revocation required")
    row = await _attachment_or_404(db, room_id, attachment_id)
    if row.state in {"revoked", "expired", "failed"}:
        return row
    was_active = row.state in {"machine_verified", "active", "revoking"}
    row.epoch += 1
    row.state = "revoking" if was_active else "revoked"
    row.revoked_at = _now() if not was_active else None
    await cancel_attachment_turns(
        db, attachment=row, reason="workspace_attachment_revoked"
    )
    await append_audit(
        db,
        attachment=row,
        event_type="revoke_requested" if was_active else "revoked",
        actor_user_id=identity.id,
        outcome=row.state,
    )
    db.add(
        ActivityLog(
            agent_id=row.agent_id,
            event_type="workspace_attachment_revoked",
            room_id=row.room_id,
            details={
                "workspace_attachment_id": row.id,
                "workspace_epoch": row.epoch,
                "mode": row.mode,
            },
        )
    )
    await db.commit()
    if was_active:
        agent = await db.get(Agent, row.agent_id)
        row.resume_after_revoke = bool(agent and agent.desired_state == "running")
        await db.commit()
        await request.app.state.agent_lifecycle.request_stop(row.agent_id)
        await request.app.state.machine_bus.send(
            row.machine_id,
            {
                "type": "workspace_revoke",
                "attachment_id": row.id,
                "agent_id": row.agent_id,
                "epoch": row.epoch,
            },
        )
    await db.refresh(row)
    return row


@router.get(
    "/{room_id}/workspace-attachments/{attachment_id}/audits",
    response_model=list[WorkspaceAuditOut],
)
async def list_audits(
    room_id: str,
    attachment_id: str,
    identity: Identity = Depends(get_current_identity),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceInvocationAudit]:
    access = await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.ROOM_READ,
    )
    if identity.kind != "user" or (
        not is_global_admin(identity)
        and (
            access.participant is None
            or access.effective_role not in {"admin", "owner"}
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="System admin or room admin audit access required",
        )
    await _attachment_or_404(db, room_id, attachment_id)
    return list(
        (
            await db.scalars(
                select(WorkspaceInvocationAudit)
                .where(WorkspaceInvocationAudit.attachment_id == attachment_id)
                .order_by(WorkspaceInvocationAudit.created_at)
            )
        ).all()
    )
