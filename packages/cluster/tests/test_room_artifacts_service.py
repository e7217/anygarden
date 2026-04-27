"""Service layer for room-artifact ingestion (#290 Phase B).

Covers ``handle_artifact_produced``: validation, fan-out to every
room the producing agent participates in, dedup via the
``(room_id, sha256)`` unique constraint, disk persistence under
``settings.artifact_files_dir``, and the list/get/delete helpers.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    RoomArtifact,
    User,
)
from doorae.rooms import artifacts as artifacts_service


@pytest.fixture()
async def env(tmp_path: Path):
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
        room_files_dir=tmp_path / "room_files",
        artifact_files_dir=tmp_path / "artifact_files",
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        owner = User(email="o@x", password_hash="h")
        project = Project(name="p")
        session.add_all([owner, project])
        await session.flush()
        room1 = Room(project_id=project.id, name="general")
        room2 = Room(project_id=project.id, name="design")
        machine = Machine(
            id="m1",
            name="m1",
            hostname="localhost",
            owner_user_id=owner.id,
        )
        session.add_all([room1, room2, machine])
        await session.flush()
        agent = Agent(
            name="codex-1",
            engine="codex",
            placed_on_machine_id="m1",
        )
        session.add(agent)
        await session.flush()
        # Place the agent in both rooms so fan-out has work to do.
        session.add_all([
            Participant(room_id=room1.id, agent_id=agent.id, role="member"),
            Participant(room_id=room2.id, agent_id=agent.id, role="member"),
        ])
        await session.commit()
        yield session_factory, config, room1, room2, agent

    await engine.dispose()


def _make_frame(
    *,
    agent_id: str,
    filename: str = "snap.png",
    mime: str = "image/png",
    body: bytes = b"\x89PNG\r\n\x1a\n binary",
) -> dict:
    return {
        "type": "room_artifact_produced",
        "agent_id": agent_id,
        "filename": filename,
        "mime": mime,
        "content_b64": base64.b64encode(body).decode("ascii"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
    }


class TestHandleArtifactProduced:
    @pytest.mark.asyncio
    async def test_fans_out_to_every_room_agent_is_in(self, env) -> None:
        session_factory, config, room1, room2, agent = env

        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db,
                _make_frame(agent_id=agent.id),
                artifact_files_dir=config.artifact_files_dir,
            )
        # One artifact row per room.
        assert len(inserted) == 2
        assert {a.room_id for a in inserted} == {room1.id, room2.id}

        # Disk files exist under the configured dir.
        for row in inserted:
            disk = config.artifact_files_dir / row.storage_path
            assert disk.exists()
            assert disk.stat().st_size == row.size_bytes
            assert hashlib.sha256(disk.read_bytes()).hexdigest() == row.sha256

    @pytest.mark.asyncio
    async def test_dedup_on_redelivery(self, env) -> None:
        session_factory, config, room1, _, agent = env
        frame = _make_frame(agent_id=agent.id)

        async with session_factory() as db:
            first = await artifacts_service.handle_artifact_produced(
                db, frame, artifact_files_dir=config.artifact_files_dir
            )
        # Replay the same frame — uniqueness on (room_id, sha256) means
        # both rooms see a no-op insert.
        async with session_factory() as db:
            second = await artifacts_service.handle_artifact_produced(
                db, frame, artifact_files_dir=config.artifact_files_dir
            )
        assert len(first) == 2
        assert second == []

        async with session_factory() as db:
            rows = (
                await db.execute(
                    select(RoomArtifact).where(RoomArtifact.room_id == room1.id)
                )
            ).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_rejects_disallowed_mime(self, env) -> None:
        session_factory, config, _, _, agent = env
        frame = _make_frame(
            agent_id=agent.id, filename="x.exe", mime="application/octet-stream"
        )
        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db, frame, artifact_files_dir=config.artifact_files_dir
            )
        assert inserted == []

    @pytest.mark.asyncio
    async def test_rejects_oversize(self, env) -> None:
        session_factory, config, _, _, agent = env
        big = b"\x00" * (artifacts_service.ARTIFACT_MAX_BYTES + 1)
        frame = _make_frame(agent_id=agent.id, body=big)
        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db, frame, artifact_files_dir=config.artifact_files_dir
            )
        assert inserted == []

    @pytest.mark.asyncio
    async def test_rejects_sha256_mismatch(self, env) -> None:
        session_factory, config, _, _, agent = env
        frame = _make_frame(agent_id=agent.id)
        frame["sha256"] = "f" * 64  # lie about the digest
        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db, frame, artifact_files_dir=config.artifact_files_dir
            )
        assert inserted == []

    @pytest.mark.asyncio
    async def test_handles_unknown_agent(self, env) -> None:
        session_factory, config, _, _, _ = env
        frame = _make_frame(agent_id="not-an-agent")
        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db, frame, artifact_files_dir=config.artifact_files_dir
            )
        assert inserted == []

    @pytest.mark.asyncio
    async def test_no_target_rooms_when_agent_unplaced(self, env) -> None:
        session_factory, config, _, _, agent = env
        # Yank the agent out of every room to simulate "agent exists
        # but is not currently a room participant".
        async with session_factory() as db:
            from sqlalchemy import delete

            await db.execute(
                delete(Participant).where(Participant.agent_id == agent.id)
            )
            await db.commit()

        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db,
                _make_frame(agent_id=agent.id),
                artifact_files_dir=config.artifact_files_dir,
            )
        assert inserted == []


class TestListGetDelete:
    @pytest.mark.asyncio
    async def test_list_orders_newest_first(self, env) -> None:
        session_factory, config, room1, _, agent = env
        async with session_factory() as db:
            await artifacts_service.handle_artifact_produced(
                db,
                _make_frame(agent_id=agent.id, body=b"first"),
                artifact_files_dir=config.artifact_files_dir,
            )
        async with session_factory() as db:
            await artifacts_service.handle_artifact_produced(
                db,
                _make_frame(agent_id=agent.id, body=b"second-distinct-bytes"),
                artifact_files_dir=config.artifact_files_dir,
            )

        async with session_factory() as db:
            rows = await artifacts_service.list_artifacts(db, room_id=room1.id)
        assert len(rows) == 2
        assert rows[0].created_at >= rows[1].created_at

    @pytest.mark.asyncio
    async def test_get_returns_none_for_wrong_room(self, env) -> None:
        session_factory, config, room1, room2, agent = env
        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db,
                _make_frame(agent_id=agent.id),
                artifact_files_dir=config.artifact_files_dir,
            )
        a = inserted[0]
        # Looking up the artifact in the *other* room must miss.
        other = room2.id if a.room_id == room1.id else room1.id
        async with session_factory() as db:
            row = await artifacts_service.get_artifact(
                db, room_id=other, artifact_id=a.id
            )
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_removes_row_and_disk_file(self, env) -> None:
        session_factory, config, room1, _, agent = env
        async with session_factory() as db:
            inserted = await artifacts_service.handle_artifact_produced(
                db,
                _make_frame(agent_id=agent.id),
                artifact_files_dir=config.artifact_files_dir,
            )
        target = next(a for a in inserted if a.room_id == room1.id)
        disk_path = config.artifact_files_dir / target.storage_path
        assert disk_path.exists()

        async with session_factory() as db:
            ok = await artifacts_service.delete_artifact(
                db,
                artifact_files_dir=config.artifact_files_dir,
                room_id=room1.id,
                artifact_id=target.id,
            )
        assert ok is True
        assert not disk_path.exists()

        async with session_factory() as db:
            row = await artifacts_service.get_artifact(
                db, room_id=room1.id, artifact_id=target.id
            )
        assert row is None

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_missing(self, env) -> None:
        session_factory, config, room1, _, _ = env
        async with session_factory() as db:
            ok = await artifacts_service.delete_artifact(
                db,
                artifact_files_dir=config.artifact_files_dir,
                room_id=room1.id,
                artifact_id="nope",
            )
        assert ok is False
