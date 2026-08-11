"""Test-gate E2E catalog for critical user-facing flows.

Tracks the quality-gate cases requested for issue ANY-6:
1. Agent create + room onboarding + WS session reconnect
2. Room lifecycle transitions
3. Engine failure handling + recovery
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from anygarden.agent_availability import NO_MACHINE_FOR_ENGINE
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import create_message_fts
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Machine,
    MachineEngine,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus


class _BusWS:
    async def send_text(self, _data: str) -> None:
        return None


@pytest_asyncio.fixture()
async def gate_catalog_env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret="unit-test-secret",
        log_level="WARNING",
    )
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if engine.dialect.name == "sqlite":
            await create_message_fts(conn)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=session_factory, machine_bus=bus)

    async with session_factory() as db:
        admin = User(email="admin@anygarden.io", password_hash="x", is_admin=True)
        db.add(admin)
        await db.flush()

        project = Project(name="qa-e2e-catalog")
        db.add(project)
        await db.flush()

        room = Room(project_id=project.id, name="gate-room")
        db.add(room)
        await db.flush()
        db.add(Participant(room_id=room.id, user_id=admin.id, role="admin"))

        machine = Machine(
            name="catalog-machine",
            hostname="localhost",
            owner_user_id=admin.id,
            status="online",
            max_agents=6,
            cpu_cores=4,
            memory_gb=8.0,
        )
        db.add(machine)
        await db.flush()
        db.add(MachineEngine(machine_id=machine.id, engine="echo"))
        await db.commit()

        await bus.register(machine.id, _BusWS())

        admin_token = create_user_token(
            admin.id,
            admin.email,
            admin.is_admin,
            secret=config.jwt_secret,
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.machine_bus = bus
        app.state.agent_lifecycle = lifecycle

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "app": app,
                "client": client,
                "config": config,
                "factory": session_factory,
                "admin": admin,
                "admin_token": admin_token,
                "project": project,
                "room": room,
                "machine_id": machine.id,
            }

    await bus.unregister(machine.id)
    await engine.dispose()


class TestGateCatalogScenarios:
    @pytest.mark.asyncio
    async def test_01_agent_create_onboard_session_reconnect(self, gate_catalog_env):
        """에이전트 생성, 룸 참가, WS 재연결을 한 플로우로 점검한다."""
        client = gate_catalog_env["client"]
        app = gate_catalog_env["app"]
        auth = {"Authorization": f"Bearer {gate_catalog_env['admin_token']}"}
        room = gate_catalog_env["room"]
        factory = gate_catalog_env["factory"]

        create = await client.post(
            "/api/v1/agents",
            json={"engine": "echo", "name": "gate-agent"},
            headers=auth,
        )
        assert create.status_code == 201
        agent_id = create.json()["id"]
        assert create.json()["desired_state"] == "running"

        agent_token = generate_token()
        token_hash, hint = hash_agent_token(agent_token)
        async with factory() as db:
            db.add(
                AgentToken(
                    agent_id=agent_id,
                    token_hash=token_hash,
                    lookup_hint=hint,
                )
            )
            await db.commit()

        part = await client.post(
            f"/api/v1/rooms/{room.id}/participants",
            json={"agent_id": agent_id, "role": "member"},
            headers=auth,
        )
        assert part.status_code == 201

        with TestClient(app) as tc:
            with tc.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=[
                    "anygarden.v1",
                    f"bearer.{gate_catalog_env['admin_token']}",
                ],
            ) as user_ws:
                user_hello = json.loads(user_ws.receive_text())
                assert user_hello["type"] == "welcome"

                user_ws.send_text(
                    json.dumps({"type": "send", "content": "안녕, 방 상태가 궁금해요."})
                )
                user_echo = json.loads(user_ws.receive_text())
                assert user_echo["type"] == "message"
                first_seq = user_echo["seq"]

                user_ws.send_text(
                    json.dumps({"type": "send", "content": "재연결 후 복구할 메시지"})
                )
                missed_echo = json.loads(user_ws.receive_text())
                assert missed_echo["type"] == "message"
                missed_seq = missed_echo["seq"]

            with tc.websocket_connect(
                f"/ws/rooms/{room.id}?since_seq={first_seq}",
                subprotocols=[
                    "anygarden.v1",
                    f"bearer.{gate_catalog_env['admin_token']}",
                ],
            ) as user_ws:
                _user_hello = json.loads(user_ws.receive_text())
                replay = json.loads(user_ws.receive_text())
                assert replay["type"] == "message"
                assert replay["seq"] == missed_seq
                assert replay["content"] == "재연결 후 복구할 메시지"

            with tc.websocket_connect(
                f"/ws/rooms/{room.id}",
                subprotocols=["anygarden.v1", f"bearer.{agent_token}"],
            ) as agent_ws:
                agent_hello = json.loads(agent_ws.receive_text())
                assert agent_hello["type"] == "welcome"

                agent_ws.send_text(
                    json.dumps({"type": "send", "content": "에이전트 준비 완료."})
                )
                agent_echo = json.loads(agent_ws.receive_text())
                assert agent_echo["type"] == "message"

            history = await client.get(
                f"/api/v1/rooms/{room.id}/messages?since_seq=0&limit=20",
                headers=auth,
            )
            assert history.status_code == 200
            assert isinstance(history.json(), list)

    @pytest.mark.asyncio
    async def test_02_room_lifecycle_flow(self, gate_catalog_env):
        """룸 생성/변경/보관/복원/삭제를 한 번에 점검한다."""
        client = gate_catalog_env["client"]
        auth = {"Authorization": f"Bearer {gate_catalog_env['admin_token']}"}
        project = gate_catalog_env["project"]

        create = await client.post(
            "/api/v1/rooms",
            json={"project_id": project.id, "name": "flow-room"},
            headers=auth,
        )
        assert create.status_code == 201
        room_id = create.json()["id"]

        rename = await client.patch(
            f"/api/v1/rooms/{room_id}",
            json={"name": "flow-room-renamed"},
            headers=auth,
        )
        assert rename.status_code == 200
        assert rename.json()["name"] == "flow-room-renamed"

        archive = await client.post(
            f"/api/v1/rooms/{room_id}/archive",
            headers=auth,
        )
        assert archive.status_code == 200
        assert archive.json()["archived_at"] is not None

        unarchive = await client.post(
            f"/api/v1/rooms/{room_id}/unarchive",
            headers=auth,
        )
        assert unarchive.status_code == 200
        assert unarchive.json()["archived_at"] is None

        delete = await client.delete(
            f"/api/v1/rooms/{room_id}",
            headers=auth,
        )
        assert delete.status_code == 204

        detail = await client.get(f"/api/v1/rooms/{room_id}", headers=auth)
        assert detail.status_code == 404

    @pytest.mark.asyncio
    async def test_03_engine_failure_and_recovery(self, gate_catalog_env):
        """미지원 엔진 생성 시 장애 코드가 노출되고, 지원이 복구되면
        재시작으로 장애가 해제되는지 확인한다."""
        client = gate_catalog_env["client"]
        auth = {"Authorization": f"Bearer {gate_catalog_env['admin_token']}"}
        factory = gate_catalog_env["factory"]
        machine_id = gate_catalog_env["machine_id"]

        create = await client.post(
            "/api/v1/agents",
            json={"engine": "ghost-model", "name": "resilient-agent"},
            headers=auth,
        )
        assert create.status_code == 201
        agent_id = create.json()["id"]

        failed = await client.get(f"/api/v1/agents/{agent_id}", headers=auth)
        assert failed.status_code == 200
        unavailable = failed.json()["unavailable_reason"]
        assert unavailable is not None
        assert unavailable["code"] == NO_MACHINE_FOR_ENGINE

        # 같은 머신이 새 엔진을 지원하도록 등록한 뒤, 시작을 재요청한다.
        async with factory() as db:
            db.add(MachineEngine(machine_id=machine_id, engine="ghost-model"))
            await db.commit()

        restart = await client.post(
            f"/api/v1/agents/{agent_id}/start",
            headers=auth,
        )
        assert restart.status_code == 200

        recovered = await client.get(f"/api/v1/agents/{agent_id}", headers=auth)
        assert recovered.status_code == 200
        assert recovered.json()["unavailable_reason"] is None
