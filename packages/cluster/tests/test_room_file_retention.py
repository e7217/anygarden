"""#1018 — files of a room archived longer than the retention period are
removed. Agent artifacts keep a tombstone row (``purged_at``) so links say
the file expired; shared files a person uploaded are deleted outright."""

from __future__ import annotations

import io
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Project, Room, RoomArtifact, RoomSharedFile, User
from anygarden.rooms import artifacts as artifacts_service
from anygarden.rooms import shared_files as shared_files_service
from anygarden.rooms.file_retention import sweep_archived_room_files
from cryptography.fernet import Fernet
from sqlalchemy import select

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.req("ROOM-15")


@pytest.fixture()
async def env(tmp_path: Path):
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
        room_files_dir=tmp_path / "room_files",
        artifact_files_dir=tmp_path / "artifact_files",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        owner = User(email="o@x", password_hash="h")
        project = Project(name="p")
        db.add_all([owner, project])
        await db.flush()
        rooms = {
            "old": Room(project_id=project.id, name="old", archived_at=NOW - timedelta(days=31)),
            "recent": Room(project_id=project.id, name="recent", archived_at=NOW - timedelta(days=5)),
            "active": Room(project_id=project.id, name="active"),
        }
        db.add_all(rooms.values())
        await db.commit()
        ids = {key: room.id for key, room in rooms.items()}
    yield factory, config, ids, owner.id
    await engine.dispose()


async def _artifact(factory, config, room_id: str, body: bytes) -> RoomArtifact:
    async with factory() as db:
        row, _ = await artifacts_service.store_uploaded_artifact(
            db, room_id=room_id, agent_id=None, filename="a.png", mime="image/png",
            raw=body, artifact_files_dir=config.artifact_files_dir, max_size_bytes=1024,
        )
        return row


async def _shared(factory, config, room_id: str, name: str) -> RoomSharedFile:
    async with factory() as db:
        return await shared_files_service.upload_file(
            db, room_files_dir=config.room_files_dir, room_id=room_id,
            uploader_user_id=None, filename=name, mime="text/markdown",
            stream=io.BytesIO(b"# brief"),
        )


async def _sweep(factory, config, days: int = 30, batch_size: int = 500):
    async with factory() as db:
        return await sweep_archived_room_files(
            db, artifact_files_dir=config.artifact_files_dir,
            room_files_dir=config.room_files_dir, retention_days=days,
            now=NOW, batch_size=batch_size,
        )


async def test_only_rooms_archived_past_the_period_lose_their_files(env) -> None:
    factory, config, ids, _ = env
    old = await _artifact(factory, config, ids["old"], b"old")
    recent = await _artifact(factory, config, ids["recent"], b"recent")
    active = await _artifact(factory, config, ids["active"], b"active")
    old_shared = await _shared(factory, config, ids["old"], "brief.md")
    kept_shared = await _shared(factory, config, ids["recent"], "brief.md")

    result = await _sweep(factory, config)

    assert result.artifacts == 1 and result.artifact_bytes == 3
    assert result.shared_files == [(ids["old"], "brief.md")]
    assert not (config.artifact_files_dir / old.storage_path).exists()
    assert (config.artifact_files_dir / recent.storage_path).exists()
    assert (config.artifact_files_dir / active.storage_path).exists()
    assert not (config.room_files_dir / old_shared.storage_path).exists()
    assert (config.room_files_dir / kept_shared.storage_path).exists()
    async with factory() as db:
        rows = {r.id: r for r in await db.scalars(select(RoomArtifact))}
        assert rows[old.id].purged_at is not None
        assert rows[recent.id].purged_at is None and rows[active.id].purged_at is None
        shared = list(await db.scalars(select(RoomSharedFile)))
        assert [s.id for s in shared] == [kept_shared.id]


async def test_sweep_is_idempotent_and_batched(env) -> None:
    factory, config, ids, _ = env
    for i in range(3):
        await _artifact(factory, config, ids["old"], f"b{i}".encode())

    first = await _sweep(factory, config, batch_size=2)
    second = await _sweep(factory, config, batch_size=2)
    third = await _sweep(factory, config, batch_size=2)

    assert (first.artifacts, second.artifacts, third.artifacts) == (2, 1, 0)


async def test_a_missing_blob_is_still_marked(env) -> None:
    factory, config, ids, _ = env
    row = await _artifact(factory, config, ids["old"], b"gone")
    (config.artifact_files_dir / row.storage_path).unlink()

    assert (await _sweep(factory, config)).artifacts == 1


async def test_unarchived_room_is_left_alone(env) -> None:
    factory, config, ids, _ = env
    row = await _artifact(factory, config, ids["old"], b"x")
    async with factory() as db:
        room = await db.get(Room, ids["old"])
        room.archived_at = None
        await db.commit()

    assert (await _sweep(factory, config)).artifacts == 0
    assert (config.artifact_files_dir / row.storage_path).exists()


async def test_same_bytes_uploaded_again_revive_the_purged_row(env) -> None:
    """After an unarchive, the same file brings old links back to life."""
    factory, config, ids, _ = env
    row = await _artifact(factory, config, ids["old"], b"chart")
    await _sweep(factory, config)
    async with factory() as db:
        room = await db.get(Room, ids["old"])
        room.archived_at = None
        await db.commit()

    again = await _artifact(factory, config, ids["old"], b"chart")

    assert again.id == row.id
    assert again.purged_at is None
    assert (config.artifact_files_dir / row.storage_path).read_bytes() == b"chart"


async def test_zero_days_disables_the_sweep(env) -> None:
    factory, config, ids, _ = env
    await _artifact(factory, config, ids["old"], b"x")
    assert (await _sweep(factory, config, days=0)).artifacts == 0
