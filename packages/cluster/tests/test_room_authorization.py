"""Unit tests for the shared room authorization contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import GuestClaims, UserClaims
from anygarden.db.models import Agent, Participant, Project, Room, Task, User
from anygarden.rooms.authorization import (
    Capability,
    accessible_room_ids,
    require_capability,
    resolve_access,
    validate_room_role,
    validate_room_visibility,
)
from fastapi import HTTPException


async def _seed_room(db):
    project = Project(name="authz")
    room = Room(project=project, name="private")
    archived = Room(
        project=project,
        name="archived",
        archived_at=datetime.now(UTC),
    )
    owner = User(email="owner@example.com", password_hash="x")
    admin = User(email="admin@example.com", password_hash="x", is_admin=True)
    member = User(email="member@example.com", password_hash="x")
    observer = User(email="observer@example.com", password_hash="x")
    outsider = User(email="outsider@example.com", password_hash="x")
    guest = User(is_anonymous=True, display_name="guest")
    agent = Agent(name="agent", engine="codex-cli")
    other_agent = Agent(name="other", engine="codex-cli")
    db.add_all(
        [
            project,
            room,
            archived,
            owner,
            admin,
            member,
            observer,
            outsider,
            guest,
            agent,
            other_agent,
        ]
    )
    await db.flush()
    participants = {
        "owner": Participant(room_id=room.id, user_id=owner.id, role="owner"),
        "member": Participant(room_id=room.id, user_id=member.id, role="member"),
        "observer": Participant(room_id=room.id, user_id=observer.id, role="observer"),
        "guest": Participant(room_id=room.id, user_id=guest.id, role="member"),
        # Deliberately corrupt-looking owner role: effective agent role must
        # still be clamped to member.
        "agent": Participant(room_id=room.id, agent_id=agent.id, role="owner"),
        "other_agent": Participant(
            room_id=room.id, agent_id=other_agent.id, role="member"
        ),
        "archived_member": Participant(
            room_id=archived.id, user_id=member.id, role="member"
        ),
        "archived_owner": Participant(
            room_id=archived.id, user_id=owner.id, role="owner"
        ),
    }
    db.add_all(participants.values())
    await db.flush()
    return {
        "room": room,
        "archived": archived,
        "users": {
            "owner": owner,
            "admin": admin,
            "member": member,
            "observer": observer,
            "outsider": outsider,
            "guest": guest,
        },
        "agents": {"agent": agent, "other": other_agent},
        "participants": participants,
    }


def _user_identity(user: User) -> Identity:
    return Identity(
        kind="user",
        id=user.id,
        claims=UserClaims(
            user_id=user.id,
            email=user.email or "",
            is_admin=bool(user.is_admin),
        ),
    )


@pytest.mark.asyncio
async def test_outsider_gets_403_before_room_existence_is_revealed(db) -> None:
    env = await _seed_room(db)
    identity = _user_identity(env["users"]["outsider"])

    for room_id in (env["room"].id, "does-not-exist"):
        with pytest.raises(HTTPException) as exc:
            await resolve_access(db, room_id=room_id, identity=identity)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_global_admin_bypasses_membership_but_not_missing_room(db) -> None:
    env = await _seed_room(db)
    identity = _user_identity(env["users"]["admin"])

    access = await require_capability(
        db,
        room_id=env["room"].id,
        identity=identity,
        capability=Capability.MEMBER_MANAGE,
    )
    assert access.is_global_admin is True
    assert access.participant is None

    with pytest.raises(HTTPException) as exc:
        await resolve_access(db, room_id="does-not-exist", identity=identity)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_accessible_room_ids_applies_same_membership_and_guest_scope(db) -> None:
    env = await _seed_room(db)
    room_id = env["room"].id
    archived_id = env["archived"].id

    assert await accessible_room_ids(
        db, identity=_user_identity(env["users"]["member"])
    ) == frozenset({room_id, archived_id})
    assert (
        await accessible_room_ids(db, identity=_user_identity(env["users"]["outsider"]))
        == frozenset()
    )
    assert await accessible_room_ids(
        db, identity=_user_identity(env["users"]["admin"])
    ) == frozenset({room_id, archived_id})

    guest = env["users"]["guest"]
    guest_identity = Identity(
        kind="guest",
        id=guest.id,
        claims=GuestClaims(
            user_id=guest.id,
            room_id=room_id,
            invite_id="invite",
            display_name="guest",
        ),
    )
    assert await accessible_room_ids(db, identity=guest_identity) == frozenset(
        {room_id}
    )

    agent = env["agents"]["agent"]
    assert await accessible_room_ids(
        db, identity=Identity(kind="agent", id=agent.id)
    ) == frozenset({room_id})


@pytest.mark.asyncio
async def test_observer_is_read_only_and_member_can_chat_create_tasks(db) -> None:
    env = await _seed_room(db)
    room_id = env["room"].id
    observer = _user_identity(env["users"]["observer"])
    member = _user_identity(env["users"]["member"])

    for capability in (
        Capability.ROOM_READ,
        Capability.TASK_READ,
        Capability.FILE_READ,
        Capability.ARTIFACT_READ,
    ):
        await require_capability(
            db, room_id=room_id, identity=observer, capability=capability
        )

    with pytest.raises(HTTPException) as exc:
        await require_capability(
            db,
            room_id=room_id,
            identity=observer,
            capability=Capability.MESSAGE_SEND,
        )
    assert exc.value.status_code == 403

    for capability in (Capability.MESSAGE_SEND, Capability.TASK_CREATE):
        await require_capability(
            db, room_id=room_id, identity=member, capability=capability
        )


@pytest.mark.asyncio
async def test_guest_is_bound_to_one_room_and_has_no_task_access(db) -> None:
    env = await _seed_room(db)
    room = env["room"]
    guest = env["users"]["guest"]
    identity = Identity(
        kind="guest",
        id=guest.id,
        claims=GuestClaims(
            user_id=guest.id,
            room_id=room.id,
            invite_id="invite",
            display_name="guest",
        ),
    )

    await require_capability(
        db,
        room_id=room.id,
        identity=identity,
        capability=Capability.MESSAGE_SEND,
    )
    with pytest.raises(HTTPException) as exc:
        await require_capability(
            db,
            room_id=room.id,
            identity=identity,
            capability=Capability.TASK_READ,
        )
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as exc:
        await resolve_access(db, room_id=env["archived"].id, identity=identity)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_agent_role_is_clamped_and_only_own_task_status_can_change(db) -> None:
    env = await _seed_room(db)
    room = env["room"]
    participant = env["participants"]["agent"]
    agent = env["agents"]["agent"]
    identity = Identity(kind="agent", id=agent.id)
    own_task = Task(
        room_id=room.id,
        title="mine",
        assignee_participant_id=participant.id,
    )
    other_task = Task(
        room_id=room.id,
        title="not mine",
        assignee_participant_id=env["participants"]["other_agent"].id,
    )
    db.add_all([own_task, other_task])
    await db.flush()

    access = await resolve_access(db, room_id=room.id, identity=identity)
    assert access.effective_role == "member"

    with pytest.raises(HTTPException) as exc:
        await require_capability(
            db,
            room_id=room.id,
            identity=identity,
            capability=Capability.MEMBER_MANAGE,
        )
    assert exc.value.status_code == 403

    await require_capability(
        db,
        room_id=room.id,
        identity=identity,
        capability=Capability.TASK_UPDATE,
        task=own_task,
        changed_fields={"status"},
    )

    for task, fields in ((other_task, {"status"}), (own_task, {"title"})):
        with pytest.raises(HTTPException) as exc:
            await require_capability(
                db,
                room_id=room.id,
                identity=identity,
                capability=Capability.TASK_UPDATE,
                task=task,
                changed_fields=fields,
            )
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_archive_blocks_writes_but_keeps_reads_and_owner_escape_hatches(
    db,
) -> None:
    env = await _seed_room(db)
    room_id = env["archived"].id
    member = _user_identity(env["users"]["member"])
    owner = _user_identity(env["users"]["owner"])
    global_admin = _user_identity(env["users"]["admin"])

    await require_capability(
        db,
        room_id=room_id,
        identity=member,
        capability=Capability.ROOM_READ,
    )
    with pytest.raises(HTTPException) as exc:
        await require_capability(
            db,
            room_id=room_id,
            identity=member,
            capability=Capability.MESSAGE_SEND,
        )
    assert exc.value.status_code == 409

    for identity in (owner, global_admin):
        for capability in (Capability.ROOM_UNARCHIVE, Capability.ROOM_DELETE):
            await require_capability(
                db,
                room_id=room_id,
                identity=identity,
                capability=capability,
            )


def test_role_and_visibility_validation_are_private_mvp_only() -> None:
    assert validate_room_role("observer") == "observer"
    assert validate_room_visibility("private") == "private"
    with pytest.raises(ValueError):
        validate_room_role("moderator")
    with pytest.raises(ValueError):
        validate_room_visibility("public")
