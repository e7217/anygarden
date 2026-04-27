"""Schema + model round-trip for ``room_artifacts`` (#290 Phase B).

Migration 035 introduces the agent → user artifact channel as a
table distinct from ``room_shared_files`` (the user → agent flow).
These tests guard:

- migration adds the table with the expected DDL,
- the ``RoomArtifact`` SQLAlchemy model can be inserted/selected
  through the standard async session,
- the ``(room_id, sha256)`` unique constraint enforces idempotent
  re-delivery (the server-side dedup the plan §3.2 D5 calls for),
- ``ON DELETE SET NULL`` on ``produced_by_agent_id`` keeps artifacts
  alive when their producer agent row is deleted,
- ``ON DELETE CASCADE`` on ``room_id`` cleans up artifacts when the
  room is dropped.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import IntegrityError

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Machine,
    Project,
    Room,
    RoomArtifact,
    User,
)


def _alembic_config(db_path: str) -> Config:
    cfg = Config()
    script_location = (
        Path(__file__).resolve().parent.parent / "doorae" / "db" / "migrations"
    )
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


class TestRoomArtifactsMigration:
    def test_035_creates_table_with_expected_columns(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                schema = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name='room_artifacts'"
                    )
                ).scalar_one()
                assert "id VARCHAR(36) NOT NULL" in schema
                assert "room_id VARCHAR(36) NOT NULL" in schema
                assert "produced_by_agent_id VARCHAR(36)" in schema
                # produced_by_agent_id MUST be nullable (SET NULL on agent delete)
                assert "produced_by_agent_id VARCHAR(36) NOT NULL" not in schema
                assert "filename VARCHAR(255) NOT NULL" in schema
                assert "storage_path VARCHAR(512) NOT NULL" in schema
                assert "sha256 VARCHAR(64) NOT NULL" in schema
                assert "size_bytes BIGINT NOT NULL" in schema
                assert "mime VARCHAR(128) NOT NULL" in schema
                assert "uq_room_artifact_sha" in schema
                # FK behaviours
                assert "ON DELETE CASCADE" in schema  # room_id
                assert "ON DELETE SET NULL" in schema  # produced_by_agent_id

                # Index exists
                idx = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND name='ix_room_artifacts_room_id'"
                    )
                ).scalar_one_or_none()
                assert idx == "ix_room_artifacts_room_id"
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def test_035_round_trip_downgrade_drops_table(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        try:
            cfg = _alembic_config(db_path)
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "034")

            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='room_artifacts'"
                    )
                ).scalar_one_or_none()
                assert row is None
            engine.dispose()
        finally:
            try:
                os.unlink(db_path)
            except OSError:
                pass


@pytest.fixture()
async def artifacts_session(tmp_path: Path):
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
        room_files_dir=tmp_path / "room_files",
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        # SQLite's PRAGMA foreign_keys defaults to OFF; we need it on
        # to exercise the SET NULL / CASCADE behaviour.
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        owner = User(email="owner@x", password_hash="h")
        project = Project(name="proj")
        session.add_all([owner, project])
        await session.flush()
        room = Room(project_id=project.id, name="general")
        machine = Machine(
            id="m1",
            name="m1",
            hostname="localhost",
            owner_user_id=owner.id,
        )
        session.add_all([room, machine])
        await session.flush()
        agent = Agent(
            name="codex-1",
            engine="codex",
            placed_on_machine_id="m1",
        )
        session.add(agent)
        await session.flush()
        await session.commit()
        yield session, room, agent, session_factory

    await engine.dispose()


class TestRoomArtifactModel:
    @pytest.mark.asyncio
    async def test_insert_and_read_back(self, artifacts_session) -> None:
        session, room, agent, _ = artifacts_session
        artifact = RoomArtifact(
            room_id=room.id,
            produced_by_agent_id=agent.id,
            filename="screenshot.png",
            storage_path=f"{room.id}/abc",
            sha256="a" * 64,
            size_bytes=2048,
            mime="image/png",
        )
        session.add(artifact)
        await session.commit()

        rows = (
            await session.execute(
                select(RoomArtifact).where(RoomArtifact.room_id == room.id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].filename == "screenshot.png"
        assert rows[0].mime == "image/png"
        assert rows[0].produced_by_agent_id == agent.id

    @pytest.mark.asyncio
    async def test_unique_constraint_room_sha256(self, artifacts_session) -> None:
        session, room, agent, _ = artifacts_session
        sha = "b" * 64
        session.add(
            RoomArtifact(
                room_id=room.id,
                produced_by_agent_id=agent.id,
                filename="a.png",
                storage_path=f"{room.id}/a",
                sha256=sha,
                size_bytes=1,
                mime="image/png",
            )
        )
        await session.commit()
        session.add(
            RoomArtifact(
                room_id=room.id,
                produced_by_agent_id=agent.id,
                filename="dup.png",
                storage_path=f"{room.id}/dup",
                sha256=sha,
                size_bytes=1,
                mime="image/png",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

    @pytest.mark.asyncio
    async def test_set_null_on_producer_delete(self, artifacts_session) -> None:
        session, room, agent, session_factory = artifacts_session
        session.add(
            RoomArtifact(
                room_id=room.id,
                produced_by_agent_id=agent.id,
                filename="x.png",
                storage_path=f"{room.id}/x",
                sha256="c" * 64,
                size_bytes=1,
                mime="image/png",
            )
        )
        await session.commit()

        # Use a separate session so we don't fight the IDENTITY map.
        async with session_factory() as s2:
            await s2.execute(text("PRAGMA foreign_keys=ON"))
            await s2.execute(delete(Agent).where(Agent.id == agent.id))
            await s2.commit()

        async with session_factory() as s3:
            row = (
                await s3.execute(select(RoomArtifact).where(RoomArtifact.room_id == room.id))
            ).scalar_one()
            # Artifact survives, producer reference cleared.
            assert row.produced_by_agent_id is None
            assert row.filename == "x.png"

    @pytest.mark.asyncio
    async def test_cascade_on_room_delete(self, artifacts_session) -> None:
        session, room, agent, session_factory = artifacts_session
        session.add(
            RoomArtifact(
                room_id=room.id,
                produced_by_agent_id=agent.id,
                filename="y.png",
                storage_path=f"{room.id}/y",
                sha256="d" * 64,
                size_bytes=1,
                mime="image/png",
            )
        )
        await session.commit()

        async with session_factory() as s2:
            await s2.execute(text("PRAGMA foreign_keys=ON"))
            await s2.execute(delete(Room).where(Room.id == room.id))
            await s2.commit()

        async with session_factory() as s3:
            rows = (
                await s3.execute(select(RoomArtifact))
            ).scalars().all()
            assert rows == []
