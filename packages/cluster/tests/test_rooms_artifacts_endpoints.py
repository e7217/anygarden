"""HTTP endpoints for room artifacts (#290 Phase B).

End-to-end through the FastAPI app: artifacts ingested directly via
the service layer (the WS path is covered separately) and then
exercised through ``GET /artifacts``, ``GET /artifacts/{id}``, and
``DELETE /artifacts/{id}``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.rooms import artifacts as artifacts_service


@pytest_asyncio.fixture()
async def env(tmp_path: Path):
    config = AnygardenSettings(
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

    async with session_factory() as db:
        owner = User(email="o@x", password_hash="h")
        outsider = User(email="x@y", password_hash="h")
        project = Project(name="p")
        db.add_all([owner, outsider, project])
        await db.flush()
        room = Room(project_id=project.id, name="general")
        machine = Machine(
            id="m1",
            name="m1",
            hostname="localhost",
            owner_user_id=owner.id,
        )
        db.add_all([room, machine])
        await db.flush()
        agent = Agent(
            name="codex-1",
            engine="codex",
            placed_on_machine_id="m1",
        )
        db.add(agent)
        await db.flush()
        outsider_agent = Agent(
            name="codex-2",
            engine="codex",
            placed_on_machine_id="m1",
        )
        db.add(outsider_agent)
        await db.flush()
        db.add_all([
            Participant(room_id=room.id, user_id=owner.id, role="admin"),
            Participant(room_id=room.id, agent_id=agent.id, role="member"),
        ])
        agent_token = generate_token()
        hashed, hint = hash_agent_token(agent_token)
        outsider_agent_token = generate_token()
        o_hashed, o_hint = hash_agent_token(outsider_agent_token)
        db.add_all([
            AgentToken(agent_id=agent.id, token_hash=hashed, lookup_hint=hint),
            AgentToken(
                agent_id=outsider_agent.id, token_hash=o_hashed, lookup_hint=o_hint
            ),
        ])
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

        yield {
            "app": app,
            "config": config,
            "session_factory": session_factory,
            "room": room,
            "owner_token": owner_token,
            "outsider_token": outsider_token,
            "agent": agent,
            "agent_token": agent_token,
            "outsider_agent_token": outsider_agent_token,
        }
    await engine.dispose()


@pytest_asyncio.fixture()
async def client(env):
    transport = ASGITransport(app=env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_artifact(env, *, body: bytes = b"binary-bytes"):
    """Push one artifact through the service layer so the endpoints
    have something to read. Returns the inserted RoomArtifact."""
    frame = {
        "type": "room_artifact_produced",
        "agent_id": env["agent"].id,
        "filename": "snap.png",
        "mime": "image/png",
        "content_b64": base64.b64encode(body).decode("ascii"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
    }
    async with env["session_factory"]() as db:
        rows = await artifacts_service.handle_artifact_produced(
            db, frame, artifact_files_dir=env["config"].artifact_files_dir
        )
    assert rows, "fixture failed to seed artifact"
    return rows[0]


class TestList:
    async def test_owner_can_list(self, client: AsyncClient, env) -> None:
        await _seed_artifact(env)
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/artifacts",
            headers=_auth(env["owner_token"]),
        )
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["filename"] == "snap.png"
        assert rows[0]["mime"] == "image/png"
        assert rows[0]["produced_by_agent_id"] == env["agent"].id

    async def test_outsider_gets_403(self, client: AsyncClient, env) -> None:
        await _seed_artifact(env)
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/artifacts",
            headers=_auth(env["outsider_token"]),
        )
        assert resp.status_code == 403


class TestDownload:
    async def test_owner_download_returns_bytes(
        self, client: AsyncClient, env
    ) -> None:
        body = b"\x89PNG\r\n\x1a\n placeholder"
        artifact = await _seed_artifact(env, body=body)
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/artifacts/{artifact.id}",
            headers=_auth(env["owner_token"]),
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == body
        assert hashlib.sha256(resp.content).hexdigest() == artifact.sha256

    async def test_outsider_gets_403(self, client: AsyncClient, env) -> None:
        artifact = await _seed_artifact(env)
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/artifacts/{artifact.id}",
            headers=_auth(env["outsider_token"]),
        )
        assert resp.status_code == 403

    async def test_unknown_artifact_404(
        self, client: AsyncClient, env
    ) -> None:
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/artifacts/nope",
            headers=_auth(env["owner_token"]),
        )
        assert resp.status_code == 404


class TestDelete:
    async def test_owner_can_delete(self, client: AsyncClient, env) -> None:
        artifact = await _seed_artifact(env)
        disk = env["config"].artifact_files_dir / artifact.storage_path
        assert disk.exists()
        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/artifacts/{artifact.id}",
            headers=_auth(env["owner_token"]),
        )
        assert resp.status_code == 204
        assert not disk.exists()

        # Subsequent GET returns 404.
        resp = await client.get(
            f"/api/v1/rooms/{env['room'].id}/artifacts/{artifact.id}",
            headers=_auth(env["owner_token"]),
        )
        assert resp.status_code == 404

    async def test_outsider_cannot_delete(
        self, client: AsyncClient, env
    ) -> None:
        artifact = await _seed_artifact(env)
        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/artifacts/{artifact.id}",
            headers=_auth(env["outsider_token"]),
        )
        assert resp.status_code == 403
        # Disk file untouched.
        disk = env["config"].artifact_files_dir / artifact.storage_path
        assert disk.exists()

    async def test_delete_unknown_404(
        self, client: AsyncClient, env
    ) -> None:
        resp = await client.delete(
            f"/api/v1/rooms/{env['room'].id}/artifacts/nope",
            headers=_auth(env["owner_token"]),
        )
        assert resp.status_code == 404


class TestUpload:
    """#854 — agents upload a reply attachment over HTTP and get its id
    back before they send the message that references it."""

    def _url(self, env) -> str:
        return f"/api/v1/rooms/{env['room'].id}/artifacts"

    async def test_agent_upload_creates_artifact(
        self, client: AsyncClient, env
    ) -> None:
        body = b"\x89PNG\r\n\x1a\n chart"
        resp = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("chart.png", body, "image/png")},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["filename"] == "chart.png"
        assert data["mime"] == "image/png"
        assert data["size_bytes"] == len(body)
        assert data["sha256"] == hashlib.sha256(body).hexdigest()
        assert data["produced_by_agent_id"] == env["agent"].id

        download = await client.get(
            f"{self._url(env)}/{data['id']}", headers=_auth(env["owner_token"])
        )
        assert download.status_code == 200
        assert download.content == body

    async def test_same_bytes_return_existing_row(
        self, client: AsyncClient, env
    ) -> None:
        body = b"same-bytes"
        first = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("a.txt", body, "text/plain")},
        )
        second = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("a.txt", body, "text/plain")},
        )
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]

    async def test_upload_after_daemon_delivery_reuses_row(
        self, client: AsyncClient, env
    ) -> None:
        body = b"binary-bytes"
        seeded = await _seed_artifact(env, body=body)
        resp = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("snap.png", body, "image/png")},
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == seeded.id

    async def test_pdf_is_allowed(self, client: AsyncClient, env) -> None:
        resp = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("report.pdf", b"%PDF-1.7 body", "application/pdf")},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["mime"] == "application/pdf"

    async def test_mime_falls_back_to_filename(
        self, client: AsyncClient, env
    ) -> None:
        resp = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("notes.md", b"# hi", "application/octet-stream")},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["mime"] == "text/markdown"

    async def test_disallowed_mime_415(self, client: AsyncClient, env) -> None:
        resp = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("tool.exe", b"MZ", "application/x-msdownload")},
        )
        assert resp.status_code == 415

    async def test_oversize_413(self, client: AsyncClient, env) -> None:
        env["config"].artifact_upload_max_bytes = 16
        resp = await client.post(
            self._url(env),
            headers=_auth(env["agent_token"]),
            files={"upload": ("big.txt", b"x" * 17, "text/plain")},
        )
        assert resp.status_code == 413

    async def test_human_upload_403(self, client: AsyncClient, env) -> None:
        resp = await client.post(
            self._url(env),
            headers=_auth(env["owner_token"]),
            files={"upload": ("a.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 403

    async def test_non_participant_agent_403(
        self, client: AsyncClient, env
    ) -> None:
        resp = await client.post(
            self._url(env),
            headers=_auth(env["outsider_agent_token"]),
            files={"upload": ("a.txt", b"hi", "text/plain")},
        )
        assert resp.status_code == 403
