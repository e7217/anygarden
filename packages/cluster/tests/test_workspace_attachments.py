"""Phase 5 opaque workspace lease, audit and revoke regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import UserClaims
from anygarden.db.models import (
    Agent,
    AgentTurn,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    Task,
    User,
    WorkspaceAttachment,
    WorkspaceInvocationAudit,
)
from anygarden.db.repository import append_message
from anygarden.turns.service import create_turn, deliver_pending_outbox
from anygarden.workspaces.lifecycle import (
    handle_attach_receipt,
    handle_revoke_receipt,
    revoke_invalid_attachments,
)
from anygarden.workspaces.router import AttachmentCreate, VerifyBody, list_audits
from anygarden.workspaces.service import (
    append_audit,
    machine_can_activate,
    policy_hash,
)
from anygarden_machine.workspace_signing import WorkspaceReceiptSigner


class FakeManager:
    def __init__(self, participant_id: str) -> None:
        self.participant_id = participant_id
        self.frames: list[object] = []

    async def is_connected(self, participant_id: str) -> bool:
        return participant_id == self.participant_id

    async def participant_generation(self, participant_id: str) -> int:
        return 3

    async def send_to(self, participant_id: str, frame: object, **_: object) -> bool:
        self.frames.append(frame)
        return True


class FakeBus:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def send(self, machine_id: str, frame: dict) -> bool:
        self.frames.append((machine_id, frame))
        return True


class FakeLifecycle:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def request_stop(self, agent_id: str) -> None:
        self.stopped.append(agent_id)


@pytest_asyncio.fixture()
async def workspace_env(tmp_path: Path):
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    signer = WorkspaceReceiptSigner(tmp_path / "workspace-signing.key")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        user = User(email="workspace@test.com", password_hash="x", is_admin=True)
        db.add(user)
        await db.flush()
        project = Project(name="workspace-project")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="workspace-room")
        db.add(room)
        machine = Machine(
            name="workspace-machine",
            hostname="host",
            owner_user_id=user.id,
            status="online",
            control_capabilities=[
                "workspace_attach_v1",
                "workspace_write_root_v1",
                "workspace_audit_signing_v1",
                "workspace_receipt_signing_v1",
            ],
            workspace_signing_public_key=signer.public_key,
        )
        db.add(machine)
        await db.flush()
        agent = Agent(
            name="workspace-agent",
            engine="codex-cli",
            permission_level="standard",
            desired_state="running",
            actual_state="running",
            placed_on_machine_id=machine.id,
            generation=3,
        )
        db.add(agent)
        await db.flush()
        user_participant = Participant(room_id=room.id, user_id=user.id, role="owner")
        agent_participant = Participant(
            room_id=room.id, agent_id=agent.id, role="member"
        )
        db.add_all([user_participant, agent_participant])
        await db.flush()
        fingerprint = "1" * 64
        allowlist_hash = "2" * 64
        attachment = WorkspaceAttachment(
            workspace_id="ws_opaque_fixture",
            workspace_label="redacted checkout",
            machine_id=machine.id,
            agent_id=agent.id,
            room_id=room.id,
            target_participant_id=agent_participant.id,
            mode="write",
            state="active",
            epoch=7,
            fingerprint=fingerprint,
            allowlist_hash=allowlist_hash,
            policy_hash=policy_hash(
                mode="write",
                fingerprint=fingerprint,
                allowlist_hash=allowlist_hash,
            ),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            requested_by_user_id=user.id,
            room_approved_by_user_id=user.id,
            global_approved_by_user_id=user.id,
            activated_at=datetime.now(timezone.utc),
        )
        db.add(attachment)
        await db.commit()
        ids = {
            "user": user.id,
            "room": room.id,
            "machine": machine.id,
            "agent": agent.id,
            "user_participant": user_participant.id,
            "agent_participant": agent_participant.id,
            "attachment": attachment.id,
        }

    yield {"engine": engine, "factory": factory, "signer": signer, **ids}
    await engine.dispose()


def test_raw_path_is_not_part_of_attachment_request_contract() -> None:
    with pytest.raises(ValidationError):
        AttachmentCreate.model_validate(
            {
                "agent_id": "agent",
                "participant_id": "participant",
                "workspace_id": "ws_opaque_identifier",
                "mode": "write",
                "path": "/home/user/secret",
            }
        )


def test_verify_contract_accepts_only_one_way_consent_proof() -> None:
    proof = f"wcp_{'a' * 64}"
    assert VerifyBody.model_validate({"consent_proof": proof}).consent_proof
    with pytest.raises(ValidationError):
        VerifyBody.model_validate({"consent_token": "wsc_raw_secret"})
    with pytest.raises(ValidationError):
        VerifyBody.model_validate({"consent_proof": "wsc_raw_secret"})


@pytest.mark.asyncio
async def test_write_turn_requires_claimed_source_linked_task(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        message = await append_message(
            db,
            workspace_env["room"],
            workspace_env["user_participant"],
            "change the workspace",
        )
        denied = await create_turn(
            db,
            room_id=workspace_env["room"],
            participant_id=workspace_env["agent_participant"],
            agent_id=workspace_env["agent"],
            trigger_message_id=message.id,
        )
        assert denied.state == "cancelled"
        assert denied.terminal_reason == "workspace_write_requires_task"

        task = Task(
            room_id=workspace_env["room"],
            source_message_id=message.id,
            title="approved workspace change",
            status="in_progress",
            assignee_participant_id=workspace_env["agent_participant"],
            created_by=workspace_env["user"],
        )
        db.add(task)
        await db.flush()
        allowed = await create_turn(
            db,
            room_id=workspace_env["room"],
            participant_id=workspace_env["agent_participant"],
            agent_id=workspace_env["agent"],
            trigger_message_id=message.id,
            task_id=task.id,
            idempotency_key="workspace-valid-task",
        )
        assert allowed.state == "pending"
        assert allowed.workspace_attachment_id == workspace_env["attachment"]
        assert allowed.workspace_attachment_epoch == 7
        await db.commit()

    manager = FakeManager(workspace_env["agent_participant"])
    assert await deliver_pending_outbox(workspace_env["factory"], manager) == 1
    metadata = manager.frames[0].metadata
    assert metadata["workspace_attachment_id"] == workspace_env["attachment"]
    assert metadata["workspace_attachment_epoch"] == 7


@pytest.mark.asyncio
async def test_epoch_change_fences_pending_delivery(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        message = await append_message(
            db,
            workspace_env["room"],
            workspace_env["user_participant"],
            "epoch-fenced change",
        )
        task = Task(
            room_id=workspace_env["room"],
            source_message_id=message.id,
            title="epoch-fenced",
            status="in_progress",
            assignee_participant_id=workspace_env["agent_participant"],
            created_by=workspace_env["user"],
        )
        db.add(task)
        await db.flush()
        turn = await create_turn(
            db,
            room_id=workspace_env["room"],
            participant_id=workspace_env["agent_participant"],
            agent_id=workspace_env["agent"],
            trigger_message_id=message.id,
            task_id=task.id,
        )
        request_id = turn.request_id
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.epoch += 1
        await db.commit()

    manager = FakeManager(workspace_env["agent_participant"])
    assert await deliver_pending_outbox(workspace_env["factory"], manager) == 0
    assert manager.frames == []
    async with workspace_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        assert turn is not None and turn.state == "cancelled"
        assert turn.terminal_reason == "workspace_epoch_mismatch"


@pytest.mark.asyncio
async def test_archive_revokes_attachment_and_emits_opaque_stop_frame(
    workspace_env,
) -> None:
    async with workspace_env["factory"]() as db:
        room = await db.get(Room, workspace_env["room"])
        assert room is not None
        room.archived_at = datetime.now(timezone.utc)
        await db.commit()

    bus = FakeBus()
    lifecycle = FakeLifecycle()
    assert (
        await revoke_invalid_attachments(workspace_env["factory"], bus, lifecycle) == 1
    )
    assert lifecycle.stopped == [workspace_env["agent"]]
    assert len(bus.frames) == 1
    machine_id, frame = bus.frames[0]
    assert machine_id == workspace_env["machine"]
    assert frame["type"] == "workspace_revoke"
    assert "path" not in frame
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None and attachment.state == "revoking"
        assert attachment.failure_code == "workspace_room_archived"
        revoke_epoch = attachment.epoch

    assert (
        await revoke_invalid_attachments(workspace_env["factory"], bus, lifecycle) == 1
    )
    assert bus.frames[-1][1]["epoch"] == revoke_epoch


@pytest.mark.asyncio
async def test_audit_strips_paths_content_secrets_and_chains(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        await append_audit(
            db,
            attachment=attachment,
            event_type="invocation_allowed",
            prompt="do not persist this prompt",
            details={
                "path": "/home/alice/private",
                "token": "wsc_secret",
                "reason": "approved",
                "daemon_note": "denied at /srv/private token=wsc_embedded",
                "codes": ["approved", "credential=wsc_list_secret"],
            },
        )
        await append_audit(
            db,
            attachment=attachment,
            event_type="invocation_completed",
            details={"changed_count": 0, "stdout": "secret output"},
        )
        await db.commit()
        rows = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit)
                    .where(WorkspaceInvocationAudit.attachment_id == attachment.id)
                    .order_by(WorkspaceInvocationAudit.created_at)
                )
            ).all()
        )
    assert len(rows) == 2
    assert rows[0].details == {
        "reason": "approved",
        "daemon_note": "[redacted]",
        "codes": ["approved", "[redacted]"],
    }
    assert rows[0].prompt_hmac and "do not persist" not in rows[0].prompt_hmac
    assert rows[1].previous_hash == rows[0].row_hash
    assert "stdout" not in (rows[1].details or {})


@pytest.mark.asyncio
async def test_denied_receipt_projects_only_allowlisted_reason_code(
    workspace_env,
) -> None:
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.state = "requested"
        await db.commit()

    receipt = {
        "type": "workspace_attach_receipt",
        "attachment_id": workspace_env["attachment"],
        "workspace_id": "ws_opaque_fixture",
        "agent_id": workspace_env["agent"],
        "epoch": 7,
        "status": "denied",
        "reason": "failed at /home/alice/private token=wsc_raw_secret",
    }
    receipt["signature"] = workspace_env["signer"].sign(receipt)
    await handle_attach_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data=receipt,
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        assert attachment.failure_code == "machine_denied"
        audits = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit).where(
                        WorkspaceInvocationAudit.attachment_id == attachment.id
                    )
                )
            ).all()
        )
    projection = str([row.details for row in audits])
    assert "/home/alice" not in projection
    assert "wsc_raw_secret" not in projection
    assert "machine_denied" in projection


@pytest.mark.asyncio
async def test_verified_receipt_discards_untrusted_reason_text(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.state = "requested"
        await db.commit()

    capabilities = [
        "workspace_attach_v1",
        "workspace_write_root_v1",
        "workspace_audit_signing_v1",
        "workspace_receipt_signing_v1",
    ]
    receipt = {
        "type": "workspace_attach_receipt",
        "attachment_id": workspace_env["attachment"],
        "workspace_id": "ws_opaque_fixture",
        "agent_id": workspace_env["agent"],
        "epoch": 7,
        "status": "verified",
        "reason": "verified /etc/shadow authorization=wsc_raw_secret",
        "fingerprint": "1" * 64,
        "allowlist_hash": "2" * 64,
        "capabilities": capabilities,
    }
    receipt["signature"] = workspace_env["signer"].sign(receipt)
    await handle_attach_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data=receipt,
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        assert attachment.failure_code == "workspace_write_adapter_unavailable"
        audits = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit).where(
                        WorkspaceInvocationAudit.attachment_id == attachment.id
                    )
                )
            ).all()
        )
    projection = str([row.details for row in audits])
    assert "/etc/shadow" not in projection
    assert "wsc_raw_secret" not in projection
    assert "machine_verified" in projection


@pytest.mark.asyncio
async def test_attach_receipt_requires_enrolled_signing_key(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        machine = await db.get(Machine, workspace_env["machine"])
        assert attachment is not None and machine is not None
        attachment.state = "requested"
        machine.workspace_signing_public_key = None
        await db.commit()

    receipt = {
        "type": "workspace_attach_receipt",
        "attachment_id": workspace_env["attachment"],
        "workspace_id": "ws_opaque_fixture",
        "agent_id": workspace_env["agent"],
        "epoch": 7,
        "status": "denied",
        "reason": "consent_expired",
    }
    receipt["signature"] = workspace_env["signer"].sign(receipt)
    await handle_attach_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data=receipt,
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        assert attachment.state == "failed"
        assert attachment.failure_code == "workspace_receipt_key_unenrolled"
        audit = (
            await db.scalars(
                select(WorkspaceInvocationAudit).where(
                    WorkspaceInvocationAudit.attachment_id == attachment.id
                )
            )
        ).one()
        assert audit.details == {"reason": "workspace_receipt_key_unenrolled"}


@pytest.mark.asyncio
async def test_attach_receipt_signature_covers_untrusted_reason(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.state = "requested"
        await db.commit()

    receipt = {
        "type": "workspace_attach_receipt",
        "attachment_id": workspace_env["attachment"],
        "workspace_id": "ws_opaque_fixture",
        "agent_id": workspace_env["agent"],
        "epoch": 7,
        "status": "denied",
        "reason": "consent_expired",
    }
    receipt["signature"] = workspace_env["signer"].sign(receipt)
    receipt["reason"] = "failed at /root/private token=wsc_mutated"
    await handle_attach_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data=receipt,
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        assert attachment.failure_code == "workspace_receipt_signature_invalid"
        audits = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit).where(
                        WorkspaceInvocationAudit.attachment_id == attachment.id
                    )
                )
            ).all()
        )
    projection = str([row.details for row in audits])
    assert "/root/private" not in projection
    assert "wsc_mutated" not in projection


@pytest.mark.asyncio
async def test_revoke_receipt_signature_failure_stays_retryable(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.state = "revoking"
        await db.commit()

    receipt = {
        "type": "workspace_revoke_receipt",
        "attachment_id": workspace_env["attachment"],
        "agent_id": workspace_env["agent"],
        "epoch": 7,
        "status": "failed",
        "reason": "PermissionError",
    }
    receipt["signature"] = workspace_env["signer"].sign(receipt)
    receipt["reason"] = "failed at /root/private token=wsc_mutated"
    await handle_revoke_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data=receipt,
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        assert attachment.state == "revoking"
        assert attachment.failure_code == "workspace_receipt_signature_invalid"


@pytest.mark.asyncio
async def test_audit_evidence_survives_attachment_deletion(workspace_env) -> None:
    attachment_id = workspace_env["attachment"]
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, attachment_id)
        assert attachment is not None
        await append_audit(
            db,
            attachment=attachment,
            event_type="evidence_snapshot",
            outcome="preserved",
        )
        await db.commit()
        await db.delete(attachment)
        await db.commit()

    async with workspace_env["factory"]() as db:
        audits = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit).where(
                        WorkspaceInvocationAudit.attachment_id == attachment_id
                    )
                )
            ).all()
        )
    assert len(audits) == 1
    assert audits[0].event_type == "evidence_snapshot"


@pytest.mark.asyncio
async def test_audit_api_requires_system_or_room_admin(workspace_env) -> None:
    async with workspace_env["factory"]() as db:
        room_admin = User(
            email="room-admin@test.com", password_hash="x", is_admin=False
        )
        observer = User(email="observer@test.com", password_hash="x", is_admin=False)
        db.add_all([room_admin, observer])
        await db.flush()
        db.add_all(
            [
                Participant(
                    room_id=workspace_env["room"],
                    user_id=room_admin.id,
                    role="admin",
                ),
                Participant(
                    room_id=workspace_env["room"],
                    user_id=observer.id,
                    role="observer",
                ),
            ]
        )
        await db.commit()

        admin_identity = Identity(
            kind="user",
            id=room_admin.id,
            claims=UserClaims(
                user_id=room_admin.id,
                email=room_admin.email or "",
                is_admin=False,
            ),
        )
        assert (
            await list_audits(
                workspace_env["room"],
                workspace_env["attachment"],
                identity=admin_identity,
                db=db,
            )
            == []
        )

        observer_identity = Identity(
            kind="user",
            id=observer.id,
            claims=UserClaims(
                user_id=observer.id,
                email=observer.email or "",
                is_admin=False,
            ),
        )
        with pytest.raises(HTTPException) as exc_info:
            await list_audits(
                workspace_env["room"],
                workspace_env["attachment"],
                identity=observer_identity,
                db=db,
            )
        assert exc_info.value.status_code == 403


def test_legacy_or_unsupported_daemon_and_engine_fail_closed() -> None:
    machine = SimpleNamespace(control_capabilities=["workspace_attach_v1"])
    agent = SimpleNamespace(engine="codex-cli", permission_level="standard")
    allowed, reason = machine_can_activate(
        machine=machine,
        agent=agent,
        mode="write",
        receipt_capabilities=["workspace_attach_v1"],
    )
    assert allowed is False
    assert reason == "workspace_receipt_signing_unavailable"

    machine.control_capabilities = [
        "workspace_attach_v1",
        "workspace_write_root_v1",
        "workspace_audit_signing_v1",
        "workspace_receipt_signing_v1",
    ]
    allowed, reason = machine_can_activate(
        machine=machine,
        agent=agent,
        mode="write",
        receipt_capabilities=machine.control_capabilities,
    )
    assert allowed is False
    assert reason == "workspace_write_adapter_unavailable"

    machine.control_capabilities = [
        "workspace_attach_v1",
        "workspace_read_root_v1",
        "workspace_audit_signing_v1",
        "workspace_receipt_signing_v1",
    ]
    agent.engine = "claude-code"
    agent.permission_level = "restricted"
    allowed, reason = machine_can_activate(
        machine=machine,
        agent=agent,
        mode="read",
        receipt_capabilities=machine.control_capabilities,
    )
    assert allowed is False
    assert reason == "workspace_engine_unsupported"
