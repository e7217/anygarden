"""Tests for GET /api/v1/rooms/{id}/token-stats (#157 Phase C)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Machine,
    Message,
    Participant,
    Project,
    Room,
    User,
)
from doorae.rooms.token_stats import (
    estimate_tokens,
    get_room_token_stats,
    serialise_window,
)


class TestEstimateTokens:
    """Pure-function unit tests — no DB."""

    def test_empty_string_returns_one(self) -> None:
        assert estimate_tokens("") == 1

    def test_short_string_minimum_one(self) -> None:
        assert estimate_tokens("abc") == 1

    def test_long_string_quarter_length(self) -> None:
        assert estimate_tokens("a" * 400) == 100

    def test_monotonic_growth(self) -> None:
        a = estimate_tokens("x" * 100)
        b = estimate_tokens("x" * 200)
        assert b > a


@pytest_asyncio.fixture()
async def stats_env():
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        regular = User(email="regular@test.com", password_hash="x", is_admin=False)
        db.add_all([admin, regular])
        await db.flush()

        machine = Machine(
            name="stats-machine",
            hostname="host-stats",
            owner_user_id=admin.id,
            status="online",
            max_agents=10,
        )
        project = Project(name="proj")
        db.add_all([machine, project])
        await db.flush()

        # Two agents
        agent_a = Agent(
            name="Researcher",
            engine="echo",
            placed_on_machine_id=machine.id,
        )
        agent_b = Agent(
            name="Writer",
            engine="echo",
            placed_on_machine_id=machine.id,
        )
        db.add_all([agent_a, agent_b])
        await db.flush()

        room = Room(name="test-room", project_id=project.id)
        db.add(room)
        await db.flush()

        part_user = Participant(user_id=admin.id, room_id=room.id, role="member")
        part_a = Participant(agent_id=agent_a.id, room_id=room.id, role="member")
        part_b = Participant(agent_id=agent_b.id, room_id=room.id, role="member")
        db.add_all([part_user, part_a, part_b])
        await db.flush()

        now = datetime.now(tz=timezone.utc)

        # Recent (within 1h): A says 400 chars → 100 tokens,
        # B says 40 chars → 10 tokens, user says 80 chars → 20 tokens
        recent_msgs = [
            Message(
                room_id=room.id,
                participant_id=part_a.id,
                content="a" * 400,
                seq=1,
                created_at=now - timedelta(minutes=5),
            ),
            Message(
                room_id=room.id,
                participant_id=part_b.id,
                content="b" * 40,
                seq=2,
                created_at=now - timedelta(minutes=3),
            ),
            Message(
                room_id=room.id,
                participant_id=part_user.id,
                content="u" * 80,
                seq=3,
                created_at=now - timedelta(minutes=1),
            ),
        ]
        # Older (within 24h but outside 1h): A says 200 → 50 tokens
        older_msgs = [
            Message(
                room_id=room.id,
                participant_id=part_a.id,
                content="a" * 200,
                seq=4,
                created_at=now - timedelta(hours=5),
            ),
        ]
        # Ancient (>24h, excluded from both windows)
        ancient_msgs = [
            Message(
                room_id=room.id,
                participant_id=part_a.id,
                content="x" * 1000,
                seq=5,
                created_at=now - timedelta(days=3),
            ),
        ]
        db.add_all(recent_msgs + older_msgs + ancient_msgs)
        await db.commit()

        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret
        )
        regular_token = create_user_token(
            regular.id, regular.email, regular.is_admin, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "admin_token": admin_token,
                "regular_token": regular_token,
                "room_id": room.id,
                "agent_a_name": agent_a.name,
                "agent_b_name": agent_b.name,
                "part_a_id": part_a.id,
                "part_b_id": part_b.id,
                "factory": factory,
            }

    await engine.dispose()


class TestRoomTokenStatsAPI:
    @pytest.mark.asyncio
    async def test_admin_can_read_stats(self, stats_env) -> None:
        client = stats_env["client"]
        resp = await client.get(
            f"/api/v1/rooms/{stats_env['room_id']}/token-stats",
            headers={"Authorization": f"Bearer {stats_env['admin_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "window_1h" in data
        assert "window_24h" in data

    @pytest.mark.asyncio
    async def test_regular_user_forbidden(self, stats_env) -> None:
        client = stats_env["client"]
        resp = await client.get(
            f"/api/v1/rooms/{stats_env['room_id']}/token-stats",
            headers={"Authorization": f"Bearer {stats_env['regular_token']}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unknown_room_returns_404(self, stats_env) -> None:
        client = stats_env["client"]
        resp = await client.get(
            "/api/v1/rooms/does-not-exist/token-stats",
            headers={"Authorization": f"Bearer {stats_env['admin_token']}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_1h_window_tokens_match_expectations(self, stats_env) -> None:
        """Recent (within 1h): A=400 chars=100 tokens, B=40=10, user=80=20 → 130."""
        client = stats_env["client"]
        resp = await client.get(
            f"/api/v1/rooms/{stats_env['room_id']}/token-stats",
            headers={"Authorization": f"Bearer {stats_env['admin_token']}"},
        )
        w1h = resp.json()["window_1h"]
        assert w1h["tokens"] == 100 + 10 + 20
        assert w1h["messages"] == 3
        assert w1h["agents"] == 3  # 2 agents + 1 user

    @pytest.mark.asyncio
    async def test_24h_window_includes_older_message(self, stats_env) -> None:
        """24h window adds the 200-char A message from 5h ago (50 tokens)."""
        client = stats_env["client"]
        resp = await client.get(
            f"/api/v1/rooms/{stats_env['room_id']}/token-stats",
            headers={"Authorization": f"Bearer {stats_env['admin_token']}"},
        )
        w24h = resp.json()["window_24h"]
        # 130 (1h) + 50 (5h-old A message)
        assert w24h["tokens"] == 130 + 50
        assert w24h["messages"] == 4

    @pytest.mark.asyncio
    async def test_per_agent_breakdown_1h(self, stats_env) -> None:
        """per_agent sorts by tokens desc; agent_name resolved from DB."""
        client = stats_env["client"]
        resp = await client.get(
            f"/api/v1/rooms/{stats_env['room_id']}/token-stats",
            headers={"Authorization": f"Bearer {stats_env['admin_token']}"},
        )
        rows = resp.json()["window_1h"]["per_agent"]
        # Highest first: A (100) > user (20) > B (10)
        names = [r["agent_name"] for r in rows]
        assert names[0] == "Researcher"
        # Middle row: user, agent_name is None
        assert None in names
        # B is last with the Writer label
        assert names[-1] == "Writer"

        # Per-row shape
        row_a = next(r for r in rows if r["agent_name"] == "Researcher")
        assert row_a["participant_id"] == stats_env["part_a_id"]
        assert row_a["tokens"] == 100
        assert row_a["messages"] == 1
        assert row_a["last_active_at"] is not None

    @pytest.mark.asyncio
    async def test_ancient_messages_excluded(self, stats_env) -> None:
        """>24h old messages never count."""
        client = stats_env["client"]
        resp = await client.get(
            f"/api/v1/rooms/{stats_env['room_id']}/token-stats",
            headers={"Authorization": f"Bearer {stats_env['admin_token']}"},
        )
        w24h = resp.json()["window_24h"]
        # The 1000-char ancient message (250 tokens) must not appear
        assert w24h["tokens"] == 180


class TestGetRoomTokenStatsDirect:
    """Unit tests for ``get_room_token_stats`` without HTTP."""

    @pytest.mark.asyncio
    async def test_empty_room_zero_stats(self, stats_env) -> None:
        factory = stats_env["factory"]
        async with factory() as db:
            other = Room(name="empty", project_id=(await db.scalar(
                select(Project.id)
            )))
            db.add(other)
            await db.commit()
            stats = await get_room_token_stats(db, other.id)
        assert stats["window_1h"].tokens == 0
        assert stats["window_1h"].messages == 0
        assert stats["window_1h"].agents == 0
        assert stats["window_1h"].per_agent == []

    @pytest.mark.asyncio
    async def test_serialise_window_shape(self, stats_env) -> None:
        factory = stats_env["factory"]
        async with factory() as db:
            stats = await get_room_token_stats(db, stats_env["room_id"])
        serialised = serialise_window(stats["window_1h"])
        assert set(serialised) == {"tokens", "messages", "agents", "per_agent"}
        row = serialised["per_agent"][0]
        assert set(row) == {
            "participant_id",
            "agent_name",
            "tokens",
            "messages",
            "last_active_at",
        }
