"""Integration tests for the room shared files REST endpoints (#246)."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    RoomSharedFile,
    User,
)


class _RecordingBus:
    """Stand-in for ``MachineBus`` — records every frame ``send`` call.

    The real ``MachineBus`` requires live FastAPI WebSocket objects to
    be useful; for the endpoint tests we only want to assert *which*
    frames were scheduled, so a list-backed double is easier to
    reason about.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, machine_id: str, frame: dict[str, Any]) -> bool:
        self.sent.append((machine_id, frame))
        return True


@pytest_asyncio.fixture()
async def shared_files_env(tmp_path: Path):
    """Set up an app + DB with one room, one user (owner), one agent
    placed on a connected machine, plus a ``_RecordingBus`` so tests
    can assert on outbound fan-out frames.

    The config pins ``room_files_dir`` to a ``tmp_path`` subdir so
    uploaded bytes stay out of the user's home and we can assert on
    the on-disk state directly.
    """
    from cryptography.fernet import Fernet

    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
        room_files_dir=tmp_path / "room_files",
    )

    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        owner = User(email="owner@example.com", password_hash="x")
        outsider = User(email="nobody@example.com", password_hash="x")
        db.add_all([owner, outsider])
        await db.flush()

        project = Project(name="proj-x")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="general")
        db.add(room)
        await db.flush()

        db.add(Participant(room_id=room.id, user_id=owner.id, role="admin"))

        machine = Machine(
            id="m1",
            name="m1",
            hostname="localhost",
            owner_user_id=owner.id,
        )
        db.add(machine)
        await db.flush()

        agent = Agent(
            name="agent-x",
            engine="codex",
            placed_on_machine_id="m1",
        )
        db.add(agent)
        await db.flush()
        db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
        await db.commit()
        await db.refresh(owner)
        await db.refresh(outsider)
        await db.refresh(room)
        await db.refresh(agent)

        owner_token = create_user_token(
            owner.id, owner.email, False, secret=config.jwt_secret
        )
        outsider_token = create_user_token(
            outsider.id, outsider.email, False, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = session_factory
        bus = _RecordingBus()
        app.state.machine_bus = bus  # type: ignore[assignment]

        yield {
            "app": app,
            "config": config,
            "session_factory": session_factory,
            "room": room,
            "owner": owner,
            "outsider": outsider,
            "agent": agent,
            "owner_token": owner_token,
            "outsider_token": outsider_token,
            "bus": bus,
        }

    await engine.dispose()


@pytest_asyncio.fixture()
async def client(shared_files_env):
    transport = ASGITransport(app=shared_files_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _wait_for_tasks() -> None:
    # Yield to the event loop long enough for FastAPI to run the
    # background tasks scheduled by the endpoint.
    for _ in range(5):
        await asyncio.sleep(0)


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestUpload:
    async def test_upload_creates_row_and_disk_file(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        resp = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("spec.md", b"# Hello\n", "text/markdown")},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["filename"] == "spec.md"
        assert body["storage_name"] == "spec.md"
        assert body["mime"] == "text/markdown"
        assert body["size_bytes"] == len(b"# Hello\n")

        # Disk file exists under room_files_dir/<room>/<id>.
        on_disk = env["config"].room_files_dir / env["room"].id / body["id"]
        assert on_disk.read_bytes() == b"# Hello\n"

    async def test_upload_fan_out_dispatches_write_frame(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        resp = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("notes.md", b"one\n", "text/markdown")},
        )
        assert resp.status_code == 201
        await _wait_for_tasks()

        frames = [f for _, f in env["bus"].sent]
        writes = [
            f for f in frames if f["type"] == "agent_memory_shared_file_write"
        ]
        assert len(writes) == 1
        assert writes[0]["agent_id"] == env["agent"].id
        assert writes[0]["storage_name"] == "notes.md"
        assert writes[0]["content"] == "one\n"

    async def test_outsider_upload_is_forbidden(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        resp = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["outsider_token"]),
            files={"upload": ("spec.md", b"x", "text/markdown")},
        )
        assert resp.status_code == 403

    async def test_upload_rejects_non_text_mime(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        resp = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("logo.png", b"\x89PNG\r\n", "image/png")},
        )
        assert resp.status_code == 415

    async def test_upload_upserts_on_same_storage_name(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        first = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("spec.md", b"v1\n", "text/markdown")},
        )
        assert first.status_code == 201
        second = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("spec.md", b"v2-newer\n", "text/markdown")},
        )
        assert second.status_code == 201
        # Same row id — upsert kept the slot but replaced the bytes.
        assert first.json()["id"] == second.json()["id"]
        assert second.json()["size_bytes"] == len(b"v2-newer\n")

        on_disk = (
            env["config"].room_files_dir / env["room"].id / second.json()["id"]
        )
        assert on_disk.read_bytes() == b"v2-newer\n"

    async def test_upload_413_on_oversize(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        # 300 KB > 256 KB ceiling
        payload = b"x" * (300 * 1024)
        resp = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("big.md", payload, "text/markdown")},
        )
        assert resp.status_code == 413


class TestList:
    async def test_lists_room_files(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("a.md", b"a", "text/markdown")},
        )
        await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("b.md", b"bb", "text/markdown")},
        )
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
        )
        assert resp.status_code == 200
        names = {f["storage_name"] for f in resp.json()}
        assert names == {"a.md", "b.md"}

    async def test_outsider_list_forbidden(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["outsider_token"]),
        )
        assert resp.status_code == 403


class TestDelete:
    async def test_delete_removes_row_disk_and_dispatches_frame(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        upload = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("spec.md", b"x", "text/markdown")},
        )
        file_id = upload.json()["id"]
        # Drain write fan-out so the delete frame is the only one we
        # assert on below.
        await _wait_for_tasks()
        env["bus"].sent.clear()

        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/files/{file_id}",
            headers=_auth_headers(env["owner_token"]),
        )
        assert resp.status_code == 204
        await _wait_for_tasks()

        # DB row gone
        async with env["session_factory"]() as session:
            assert await session.get(RoomSharedFile, file_id) is None

        # Disk file gone
        on_disk = env["config"].room_files_dir / env["room"].id / file_id
        assert not on_disk.exists()

        # Delete frame went out
        delete_frames = [
            f
            for _, f in env["bus"].sent
            if f["type"] == "agent_memory_shared_file_delete"
        ]
        assert len(delete_frames) == 1
        assert delete_frames[0]["storage_name"] == "spec.md"

    async def test_delete_missing_returns_404(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/files/does-not-exist",
            headers=_auth_headers(env["owner_token"]),
        )
        assert resp.status_code == 404

    async def test_outsider_delete_forbidden(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env
        upload = await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("spec.md", b"x", "text/markdown")},
        )
        file_id = upload.json()["id"]

        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/files/{file_id}",
            headers=_auth_headers(env["outsider_token"]),
        )
        assert resp.status_code == 403


class TestMembershipHooks:
    """Participant add/remove trigger backfill + delete fan-out so
    late-joining agents catch up on existing files and removed agents
    drop them from ``memory/shared/``."""

    async def test_add_agent_triggers_backfill(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env

        # Seed: one file already in the room (uploaded before the new
        # agent joins).
        await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("seed.md", b"seed\n", "text/markdown")},
        )
        await _wait_for_tasks()
        env["bus"].sent.clear()

        # Second agent on the same machine joins the room.
        async with env["session_factory"]() as db:
            from doorae.db.models import Agent

            agent_b = Agent(
                name="agent-b",
                engine="codex",
                placed_on_machine_id="m1",
            )
            db.add(agent_b)
            await db.commit()
            await db.refresh(agent_b)

        resp = await client.post(
            f"/api/v1/rooms/{env['room'].id}/participants",
            headers=_auth_headers(env["owner_token"]),
            json={"agent_id": agent_b.id, "role": "member"},
        )
        assert resp.status_code == 201
        await _wait_for_tasks()

        # The new agent should see a write frame for the seed file.
        targeted = [
            f
            for _, f in env["bus"].sent
            if f["type"] == "agent_memory_shared_file_write"
            and f["agent_id"] == agent_b.id
        ]
        assert len(targeted) == 1
        assert targeted[0]["storage_name"] == "seed.md"

    async def test_remove_agent_triggers_targeted_delete(
        self, client: AsyncClient, shared_files_env
    ) -> None:
        env = shared_files_env

        # Seed: one file already in the room.
        await client.post(
            f"/api/v1/rooms/{env['room'].id}/files",
            headers=_auth_headers(env["owner_token"]),
            files={"upload": ("seed.md", b"x", "text/markdown")},
        )
        await _wait_for_tasks()
        env["bus"].sent.clear()

        # Look up the participant row for the pre-seeded agent.
        async with env["session_factory"]() as db:
            from sqlalchemy import select

            from doorae.db.models import Participant

            row = (
                await db.execute(
                    select(Participant).where(
                        Participant.agent_id == env["agent"].id
                    )
                )
            ).scalar_one()
            agent_participant_id = row.id

        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/participants/{agent_participant_id}",
            headers=_auth_headers(env["owner_token"]),
        )
        assert resp.status_code == 204
        await _wait_for_tasks()

        deletes = [
            f
            for _, f in env["bus"].sent
            if f["type"] == "agent_memory_shared_file_delete"
            and f["agent_id"] == env["agent"].id
        ]
        assert len(deletes) == 1
        assert deletes[0]["storage_name"] == "seed.md"


class TestSanitize:
    """Lightweight unit coverage for the filename sanitiser — details
    that the endpoint alone can't exercise (path traversal, empty
    names, control chars)."""

    def test_strips_path_components(self) -> None:
        from doorae.rooms.shared_files import sanitize_storage_name

        assert sanitize_storage_name("../../etc/passwd") == "passwd"
        assert sanitize_storage_name("C:\\Windows\\notes.md") == "notes.md"

    def test_rejects_empty_and_dotted(self) -> None:
        from doorae.rooms.shared_files import (
            InvalidFilenameError,
            sanitize_storage_name,
        )

        for bad in ("", ".", "..", "   ", "/"):
            with pytest.raises(InvalidFilenameError):
                sanitize_storage_name(bad)

    def test_strips_control_chars(self) -> None:
        from doorae.rooms.shared_files import sanitize_storage_name

        assert sanitize_storage_name("spec\x00\x1b.md") == "spec.md"
