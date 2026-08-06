"""Phase 5 opaque workspace lease, audit and revoke regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
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
    revoke_invalid_attachments,
)
from anygarden.workspaces.router import AttachmentCreate
from anygarden.workspaces.service import (
    append_audit,
    machine_can_activate,
    policy_hash,
)


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
async def workspace_env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
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
            ],
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

    yield {"engine": engine, "factory": factory, **ids}
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
        await revoke_invalid_attachments(workspace_env["factory"], bus, lifecycle)
        == 1
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
        await revoke_invalid_attachments(workspace_env["factory"], bus, lifecycle)
        == 1
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
                    .where(
                        WorkspaceInvocationAudit.attachment_id == attachment.id
                    )
                    .order_by(WorkspaceInvocationAudit.created_at)
                )
            ).all()
        )
    assert len(rows) == 2
    assert rows[0].details == {"reason": "approved"}
    assert rows[0].prompt_hmac and "do not persist" not in rows[0].prompt_hmac
    assert rows[1].previous_hash == rows[0].row_hash
    assert "stdout" not in (rows[1].details or {})


@pytest.mark.asyncio
async def test_machine_receipt_reason_cannot_leak_path_or_secret_to_audit(
    workspace_env,
) -> None:
    """Machine-controlled receipt text is untrusted audit input.

    A daemon must not be able to smuggle a host path or credential into the
    cluster's immutable audit chain via its human-readable receipt reason.
    """
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.state = "requested"
        attachment.epoch = 9
        attachment.room_approved_by_user_id = workspace_env["user"]
        attachment.global_approved_by_user_id = workspace_env["user"]
        await db.commit()
        receipt_epoch = attachment.epoch

    await handle_attach_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data={
            "attachment_id": workspace_env["attachment"],
            "workspace_id": "ws_opaque_fixture",
            "agent_id": workspace_env["agent"],
            "epoch": receipt_epoch,
            "status": "verified",
            "reason": "verified /home/alice/private token=wsc_secret",
            "fingerprint": "1" * 64,
            "allowlist_hash": "2" * 64,
            "capabilities": [
                "workspace_attach_v1",
                "workspace_write_root_v1",
                "workspace_audit_signing_v1",
            ],
        },
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        rows = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit)
                    .where(
                        WorkspaceInvocationAudit.attachment_id
                        == workspace_env["attachment"]
                    )
                    .order_by(WorkspaceInvocationAudit.created_at)
                )
            ).all()
        )

    rendered = str([row.details for row in rows])
    assert "/home/alice/private" not in rendered
    assert "wsc_secret" not in rendered


@pytest.mark.asyncio
async def test_machine_denial_reason_cannot_leak_to_attachment_or_audit(
    workspace_env,
) -> None:
    """A daemon rejection reason must be a safe code before persistence."""
    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        attachment.state = "requested"
        attachment.epoch = 10
        await db.commit()
        receipt_epoch = attachment.epoch

    await handle_attach_receipt(
        workspace_env["factory"],
        machine_id=workspace_env["machine"],
        data={
            "attachment_id": workspace_env["attachment"],
            "workspace_id": "ws_opaque_fixture",
            "agent_id": workspace_env["agent"],
            "epoch": receipt_epoch,
            "status": "denied",
            "reason": "denied /home/alice/private token=wsc_secret",
        },
        lifecycle=FakeLifecycle(),
    )

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert attachment is not None
        rows = list(
            (
                await db.scalars(
                    select(WorkspaceInvocationAudit)
                    .where(
                        WorkspaceInvocationAudit.attachment_id
                        == workspace_env["attachment"]
                    )
                    .order_by(WorkspaceInvocationAudit.created_at)
                )
            ).all()
        )

    rendered = f"{attachment.failure_code} {[row.details for row in rows]}"
    assert "/home/alice/private" not in rendered
    assert "wsc_secret" not in rendered


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
    assert reason == "workspace_root_or_audit_capability_missing"

    machine.control_capabilities = [
        "workspace_attach_v1",
        "workspace_write_root_v1",
        "workspace_audit_signing_v1",
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
