"""Tests for the /api/v1/machines REST endpoints."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Machine, MachineToken, User
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def machines_env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    async with factory() as db:
        user = User(email="mach-api@test.com", password_hash="x", is_admin=False)
        db.add(user)
        await db.flush()

        admin_user = User(email="admin@test.com", password_hash="x", is_admin=True)
        db.add(admin_user)
        await db.flush()

        await db.commit()

        token = create_user_token(
            user.id, user.email, user.is_admin, secret=config.jwt_secret
        )
        admin_token = create_user_token(
            admin_user.id, admin_user.email, admin_user.is_admin, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.machine_bus = bus
        app.state.agent_lifecycle = lifecycle

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "token": token,
                "admin_token": admin_token,
                "factory": factory,
                "user": user,
                "admin_user": admin_user,
                "config": config,
            }

    await engine.dispose()


class TestMachinesAPI:
    @pytest.mark.asyncio
    async def test_register_machine(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "my-machine", "description": "my note"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-machine"
        assert data["description"] == "my note"
        # hostname is daemon-detected on register (#523), empty until connect.
        assert data["hostname"] == ""
        # Static system-info fields are exposed with safe defaults.
        assert data["lan_ip"] is None
        assert data["os_platform"] is None
        assert data["cpu_cores"] == 0
        assert data["memory_gb"] == 0.0
        assert data["status"] == "offline"
        assert "machine_token" in data
        assert data["machine_token"].startswith("mch_")

    @pytest.mark.asyncio
    async def test_register_ignores_stray_hostname(self, machines_env) -> None:
        """A legacy client still sending hostname must not 422 or persist it."""
        client = machines_env["client"]
        token = machines_env["token"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "legacy", "hostname": "ignored.example.com"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["hostname"] == ""

    @pytest.mark.asyncio
    async def test_list_machines(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]

        # Create a machine first
        await client.post(
            "/api/v1/machines",
            json={"name": "list-machine"},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await client.get(
            "/api/v1/machines",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        names = [m["name"] for m in data]
        assert "list-machine" in names

    @pytest.mark.asyncio
    async def test_drain_machine(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]

        # Create
        resp = await client.post(
            "/api/v1/machines",
            json={"name": "drain-machine"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        # Drain
        resp = await client.post(
            f"/api/v1/machines/{machine_id}/drain",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "draining"

    @pytest.mark.asyncio
    async def test_revoke_token(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]
        factory = machines_env["factory"]

        # Create
        resp = await client.post(
            "/api/v1/machines",
            json={"name": "revoke-machine"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        # Revoke
        resp = await client.post(
            f"/api/v1/machines/{machine_id}/tokens/revoke",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["revoked"] == 1

        # Verify token is revoked in DB
        async with factory() as db:
            result = await db.execute(
                select(MachineToken).where(
                    MachineToken.machine_id == machine_id
                )
            )
            tokens = result.scalars().all()
            assert all(t.revoked_at is not None for t in tokens)

    @pytest.mark.asyncio
    async def test_duplicate_machine_names_allowed(self, machines_env) -> None:
        """Multiple machines can have the same name (different IDs)."""
        client = machines_env["client"]
        token = machines_env["token"]

        resp1 = await client.post(
            "/api/v1/machines",
            json={"name": "same-name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp2 = await client.post(
            "/api/v1/machines",
            json={"name": "same-name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        assert resp1.json()["id"] != resp2.json()["id"]

    @pytest.mark.asyncio
    async def test_get_machine(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "get-machine"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        resp = await client.get(
            f"/api/v1/machines/{machine_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "get-machine"

    @pytest.mark.asyncio
    async def test_update_machine(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "old-name", "description": "old-desc"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/machines/{machine_id}",
            json={"name": "new-name", "description": "new-desc"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "new-name"
        assert data["description"] == "new-desc"

    @pytest.mark.asyncio
    async def test_update_machine_partial(self, machines_env) -> None:
        """PATCH with only some fields should leave others unchanged."""
        client = machines_env["client"]
        token = machines_env["token"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "partial", "description": "keep-me"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/machines/{machine_id}",
            json={"name": "partial-updated"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "partial-updated"
        assert data["description"] == "keep-me"

    @pytest.mark.asyncio
    async def test_delete_machine(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]
        factory = machines_env["factory"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "del-machine"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/machines/{machine_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == machine_id
        assert resp.json()["stopped_agents"] == []

        async with factory() as db:
            result = await db.execute(
                select(Machine).where(Machine.id == machine_id)
            )
            assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_delete_machine_refuses_with_active_agents(self, machines_env) -> None:
        """DELETE should return 409 if agents are still placed on the machine."""
        from anygarden.db.models import Agent

        client = machines_env["client"]
        token = machines_env["token"]
        factory = machines_env["factory"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "occupied"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        # Place a running agent on the machine
        async with factory() as db:
            agent = Agent(
                name="busy-agent",
                engine="openai",
                placed_on_machine_id=machine_id,
                desired_state="running",
                actual_state="running",
            )
            db.add(agent)
            await db.commit()

        resp = await client.delete(
            f"/api/v1/machines/{machine_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["detail"]["error"] == "machine_has_active_agents"
        assert body["detail"]["agent_count"] == 1

    @pytest.mark.asyncio
    async def test_delete_machine_force_stops_agents(self, machines_env) -> None:
        """DELETE ?force=true should stop all agents and delete the machine."""
        from anygarden.db.models import Agent

        client = machines_env["client"]
        token = machines_env["token"]
        factory = machines_env["factory"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "force-del"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        async with factory() as db:
            agent = Agent(
                name="force-agent",
                engine="openai",
                placed_on_machine_id=machine_id,
                desired_state="running",
                actual_state="running",
            )
            db.add(agent)
            await db.commit()
            agent_id = agent.id

        resp = await client.delete(
            f"/api/v1/machines/{machine_id}?force=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert agent_id in resp.json()["stopped_agents"]

        # Machine should be gone, agent detached and stopped
        async with factory() as db:
            result = await db.execute(
                select(Machine).where(Machine.id == machine_id)
            )
            assert result.scalar_one_or_none() is None
            result = await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one()
            assert agent.actual_state == "stopped"
            assert agent.placed_on_machine_id is None

    @pytest.mark.asyncio
    async def test_regenerate_token(self, machines_env) -> None:
        client = machines_env["client"]
        token = machines_env["token"]
        factory = machines_env["factory"]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "regen-machine"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]
        old_token = resp.json()["machine_token"]

        resp = await client.post(
            f"/api/v1/machines/{machine_id}/tokens/regenerate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        new_token = body["machine_token"]
        assert new_token.startswith("mch_")
        assert new_token != old_token
        # No daemon connected, so push must report False but mode stays default
        assert body["pushed_to_daemon"] is False
        assert body["daemon_disconnected"] is False
        assert body["mode"] == "rotate_and_push"

        # Old token should be revoked
        async with factory() as db:
            result = await db.execute(
                select(MachineToken).where(
                    MachineToken.machine_id == machine_id,
                    MachineToken.revoked_at.is_(None),
                )
            )
            active = result.scalars().all()
            assert len(active) == 1

    @pytest.mark.asyncio
    async def test_regenerate_token_pushes_to_connected_daemon(
        self, machines_env
    ) -> None:
        """Default rotation should push the new token to a connected daemon."""
        client = machines_env["client"]
        token = machines_env["token"]
        bus = machines_env["client"]._transport.app.state.machine_bus  # type: ignore[attr-defined]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "push-rotate"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        # Fake a connected daemon by registering a stub WS in the bus
        sent_frames: list[dict] = []
        closed: list[tuple[int, str]] = []

        class _StubWS:
            async def send_text(self, text: str) -> None:
                import json as _json
                sent_frames.append(_json.loads(text))

            async def close(self, code: int = 1000, reason: str = "") -> None:
                closed.append((code, reason))

        await bus.register(machine_id, _StubWS())

        resp = await client.post(
            f"/api/v1/machines/{machine_id}/tokens/regenerate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pushed_to_daemon"] is True
        assert body["daemon_disconnected"] is True
        assert body["mode"] == "rotate_and_push"

        # Verify the daemon received the new token in a rotate_token frame
        rotate_frames = [f for f in sent_frames if f.get("type") == "rotate_token"]
        assert len(rotate_frames) == 1
        assert rotate_frames[0]["new_token"] == body["machine_token"]
        # And the bus then closed the connection
        assert closed and closed[0][0] == 4001

    @pytest.mark.asyncio
    async def test_regenerate_token_revoke_only_does_not_push(
        self, machines_env
    ) -> None:
        """?revoke_only=true should disconnect WITHOUT sending the new token."""
        client = machines_env["client"]
        token = machines_env["token"]
        bus = machines_env["client"]._transport.app.state.machine_bus  # type: ignore[attr-defined]

        resp = await client.post(
            "/api/v1/machines",
            json={"name": "revoke-only-rot"},
            headers={"Authorization": f"Bearer {token}"},
        )
        machine_id = resp.json()["id"]

        sent_frames: list[dict] = []
        closed: list[tuple[int, str]] = []

        class _StubWS:
            async def send_text(self, text: str) -> None:
                import json as _json
                sent_frames.append(_json.loads(text))

            async def close(self, code: int = 1000, reason: str = "") -> None:
                closed.append((code, reason))

        await bus.register(machine_id, _StubWS())

        resp = await client.post(
            f"/api/v1/machines/{machine_id}/tokens/regenerate?revoke_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pushed_to_daemon"] is False
        assert body["daemon_disconnected"] is True
        assert body["mode"] == "revoke_only"

        # No rotate_token frame should have been sent
        rotate_frames = [f for f in sent_frames if f.get("type") == "rotate_token"]
        assert rotate_frames == []
        # But the connection was still closed
        assert closed and closed[0][0] == 4001
