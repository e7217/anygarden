"""UI availability must match the shipped daemon and server activation policy."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import UserClaims
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    User,
    WorkspaceAttachment,
)
from anygarden.node.execution import LocalDaemon
from anygarden.workspaces.router import (
    AttachmentCreate,
    approve_room,
    create_attachment,
    workspace_options,
)
from anygarden_machine.daemon import MachineDaemon
from anygarden_machine.protocol.frames import SystemInfo
from anygarden_machine.workspace_signing import WorkspaceReceiptSigner
from fastapi import HTTPException
from sqlalchemy import select


def identity(user):
    return Identity(
        kind="user",
        id=user.id,
        claims=UserClaims(user_id=user.id, email=user.email, is_admin=user.is_admin),
    )


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    # Execute the actual shipped register method; do not fake its advertised support.
    monkeypatch.setattr(
        "anygarden_machine.daemon.detect_engines",
        AsyncMock(return_value=SimpleNamespace(engines=[])),
    )
    monkeypatch.setattr(
        "anygarden_machine.daemon.collect_system_info",
        lambda: SystemInfo(hostname="test", cpu_cores=1, memory_gb=1),
    )
    signer = WorkspaceReceiptSigner(tmp_path / "receipt.key")
    send = AsyncMock()
    daemon = SimpleNamespace(
        machine_id="machine-test",
        labels={},
        _workspace_registry=SimpleNamespace(list_descriptors=list),
        _workspace_signer=signer,
        _send=send,
    )
    await MachineDaemon._register(daemon)
    frame = send.call_args.args[0]
    engine = build_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = build_session_factory(engine)
    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        owner = User(email="owner@test.com", password_hash="x", is_admin=False)
        member = User(email="member@test.com", password_hash="x", is_admin=False)
        project = Project(name="test")
        db.add_all([admin, owner, member, project])
        await db.flush()
        room = Room(project_id=project.id, name="test")
        machine = Machine(
            id="machine-test",
            name="test",
            hostname="test",
            owner_user_id=admin.id,
            status="online",
            control_capabilities=frame["control_capabilities"],
            workspace_signing_public_key=frame["workspace_signing_public_key"],
            workspace_catalog=[
                {
                    "workspace_id": "ws_repository123",
                    "label": "Repository",
                    "max_mode": "write",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                    "fingerprint": "1" * 64,
                    "allowlist_hash": "2" * 64,
                }
            ],
        )
        db.add_all([room, machine])
        await db.flush()
        agent = Agent(
            name="agent",
            engine="codex-cli",
            permission_level="restricted",
            placed_on_machine_id=machine.id,
        )
        db.add(agent)
        await db.flush()
        participant = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add_all(
            [
                participant,
                Participant(room_id=room.id, user_id=owner.id, role="owner"),
                Participant(room_id=room.id, user_id=member.id, role="member"),
            ]
        )
        await db.commit()
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    local_machine_id=machine.id,
                    config=SimpleNamespace(
                        local_node_data_dir=tmp_path / "custom-node"
                    ),
                )
            )
        )
        yield SimpleNamespace(
            db=db,
            request=request,
            room=room,
            machine=machine,
            agent=agent,
            participant=participant,
            admin=identity(admin),
            owner=identity(owner),
            member=identity(member),
        )
    await engine.dispose()


def body(env, mode="read"):
    return AttachmentCreate(
        agent_id=env.agent.id,
        participant_id=env.participant.id,
        workspace_id="ws_repository123",
        mode=mode,
    )


def enable_read(env):
    env.machine.control_capabilities = [
        *env.machine.control_capabilities,
        "workspace_read_root_v1",
        "workspace_write_root_v1",
        "workspace_audit_signing_v1",
    ]


@pytest.mark.asyncio
async def test_shipped_remote_and_integrated_daemon_are_reported_unsupported(env):
    assert LocalDaemon._register is MachineDaemon._register
    options = await workspace_options(
        env.room.id, env.agent.id, env.request, env.admin, env.db
    )
    assert options.execution_kind == "integrated"
    assert options.node_data_dir == str(
        env.request.app.state.config.local_node_data_dir
    )
    assert options.read.supported is False
    assert options.write.supported is False
    assert options.read.reason == "workspace_root_or_audit_capability_missing"
    assert "fingerprint" not in options.model_dump()["workspaces"][0]
    with pytest.raises(HTTPException) as failure:
        await create_attachment(env.room.id, body(env), env.admin, env.db)
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == options.read.reason
    assert list(await env.db.scalars(select(WorkspaceAttachment))) == []
    env.request.app.state.local_machine_id = "another-machine"
    remote = await workspace_options(
        env.room.id, env.agent.id, env.request, env.admin, env.db
    )
    assert remote.execution_kind == "remote"
    assert remote.node_data_dir is None
    assert remote.read == options.read


@pytest.mark.asyncio
async def test_contract_preserves_only_supported_read_and_rejects_write(env):
    enable_read(env)
    options = await workspace_options(
        env.room.id, env.agent.id, env.request, env.admin, env.db
    )
    assert options.read.supported is True
    assert options.write.supported is False
    assert options.write.reason == "workspace_write_adapter_unavailable"
    with pytest.raises(HTTPException) as failure:
        await create_attachment(env.room.id, body(env, "write"), env.admin, env.db)
    assert failure.value.status_code == 409
    created = await create_attachment(env.room.id, body(env), env.admin, env.db)
    assert created.state == "requested"
    assert created.mode == "read"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,permission,reason",
    [
        ("claude-sdk", "restricted", "workspace_engine_unsupported"),
        ("codex-cli", "standard", "workspace_read_requires_restricted"),
    ],
)
async def test_options_reflect_engine_and_permission_restrictions(
    env, engine, permission, reason
):
    enable_read(env)
    env.agent.engine = engine
    env.agent.permission_level = permission
    options = await workspace_options(
        env.room.id, env.agent.id, env.request, env.admin, env.db
    )
    assert options.read.supported is False
    assert options.read.reason == reason


@pytest.mark.asyncio
async def test_non_global_room_owner_can_approve_without_admin_agent_access(env):
    enable_read(env)
    created = await create_attachment(env.room.id, body(env), env.admin, env.db)
    options = await workspace_options(
        env.room.id, env.agent.id, env.request, env.owner, env.db
    )
    assert options.can_approve_room is True
    assert options.can_approve_global is False
    assert options.node_data_dir is None  # Host filesystem paths stay admin-only.
    approved = await approve_room(env.room.id, created.id, env.owner, env.db)
    assert approved.room_approved_by_user_id == env.owner.id
    assert (
        approved.global_approved_by_user_id == env.admin.id
    )  # Preserve existing approval.
    with pytest.raises(HTTPException) as failure:
        await workspace_options(
            env.room.id, env.agent.id, env.request, env.member, env.db
        )
    assert failure.value.status_code == 403
    env.room.archived_at = datetime.now(UTC)
    await env.db.flush()
    with pytest.raises(HTTPException):
        await approve_room(env.room.id, created.id, env.admin, env.db)


@pytest.mark.asyncio
async def test_expired_and_invalid_catalog_rows_are_not_offered(env):
    valid = env.machine.workspace_catalog[0]
    env.machine.workspace_catalog = [
        valid,
        {
            **valid,
            "workspace_id": "ws_expired123",
            "expires_at": "2000-01-01T00:00:00Z",
        },
        {"label": "invalid"},
    ]
    options = await workspace_options(
        env.room.id, env.agent.id, env.request, env.admin, env.db
    )
    assert [entry.workspace_id for entry in options.workspaces] == [
        valid["workspace_id"]
    ]
