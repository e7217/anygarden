"""Tests for ``GET /api/v1/agents/{agent_id}/tasks`` (#266).

Aggregates every task assigned to an agent across all the rooms it
participates in, with room metadata included so the frontend's 2차 뷰
can label each row with its origin room.
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, Participant, Room, Task, User


@pytest_asyncio.fixture()
async def env():
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
        regular = User(email="reg@test.com", password_hash="x")
        db.add_all([admin, regular])
        await db.flush()

        # Two agents — one busy (multi-room), one idle (no tasks)
        bot = Agent(name="bot", engine="echo")
        idle = Agent(name="idle", engine="echo")
        db.add_all([bot, idle])
        await db.flush()

        room_a = Room(name="Design")
        room_b = Room(name="Brand")
        room_c = Room(name="Empty")
        db.add_all([room_a, room_b, room_c])
        await db.flush()

        bot_in_a = Participant(room_id=room_a.id, agent_id=bot.id, role="member")
        bot_in_b = Participant(room_id=room_b.id, agent_id=bot.id, role="member")
        idle_in_c = Participant(room_id=room_c.id, agent_id=idle.id, role="member")
        db.add_all([bot_in_a, bot_in_b, idle_in_c])
        await db.flush()

        db.add_all(
            [
                Task(
                    room_id=room_a.id,
                    title="page",
                    status="todo",
                    assignee_participant_id=bot_in_a.id,
                ),
                Task(
                    room_id=room_a.id,
                    title="card",
                    status="in_progress",
                    assignee_participant_id=bot_in_a.id,
                ),
                Task(
                    room_id=room_b.id,
                    title="logo",
                    status="todo",
                    assignee_participant_id=bot_in_b.id,
                ),
                # Unassigned in same rooms — must NOT appear in bot's list
                Task(room_id=room_a.id, title="orphan", status="todo"),
            ]
        )
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
            "bot": bot,
            "idle": idle,
            "room_a": room_a,
            "room_b": room_b,
        }
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestListAgentTasks:
    @pytest.mark.asyncio
    async def test_admin_sees_tasks_across_rooms_with_room_metadata(self, env):
        resp = await env["client"].get(
            f"/api/v1/agents/{env['bot'].id}/tasks",
            headers=_auth(env["admin_token"]),
        )
        assert resp.status_code == 200
        rows = resp.json()
        # 3 assigned to bot (page + card in room_a, logo in room_b);
        # the unassigned "orphan" must be filtered out.
        titles = sorted(r["title"] for r in rows)
        assert titles == ["card", "logo", "page"]
        # Each row carries the originating room metadata so the 2차
        # view can render a room-name chip without a follow-up fetch.
        for row in rows:
            assert row["room_id"] in {env["room_a"].id, env["room_b"].id}
            assert "room_name" in row
            assert row["room_name"] in {"Design", "Brand"}

    @pytest.mark.asyncio
    async def test_idle_agent_returns_empty_list(self, env):
        resp = await env["client"].get(
            f"/api/v1/agents/{env['idle'].id}/tasks",
            headers=_auth(env["admin_token"]),
        )
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_non_admin_is_forbidden(self, env):
        resp = await env["client"].get(
            f"/api/v1/agents/{env['bot'].id}/tasks",
            headers=_auth(env["regular_token"]),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_filter_by_status(self, env):
        resp = await env["client"].get(
            f"/api/v1/agents/{env['bot'].id}/tasks?status=in_progress",
            headers=_auth(env["admin_token"]),
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["title"] == "card"


# ── Bulk delete (#320) ──────────────────────────────────────────────


@pytest_asyncio.fixture()
async def bulk_env():
    """Variant of ``env`` with terminal-state tasks staged for bulk delete.

    Two agents in distinct rooms; ``bot`` carries done/failed rows and a
    todo we should never sweep, while ``other`` owns its own done row to
    prove the per-agent scope.
    """
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
        regular = User(email="reg@test.com", password_hash="x")
        db.add_all([admin, regular])
        await db.flush()

        bot = Agent(name="bot", engine="echo")
        other = Agent(name="other", engine="echo")
        db.add_all([bot, other])
        await db.flush()

        room_a = Room(name="A")
        room_b = Room(name="B")
        db.add_all([room_a, room_b])
        await db.flush()

        bot_in_a = Participant(room_id=room_a.id, agent_id=bot.id, role="member")
        other_in_b = Participant(room_id=room_b.id, agent_id=other.id, role="member")
        db.add_all([bot_in_a, other_in_b])
        await db.flush()

        db.add_all(
            [
                Task(room_id=room_a.id, title="t1", status="todo", assignee_participant_id=bot_in_a.id),
                Task(room_id=room_a.id, title="t2", status="done", assignee_participant_id=bot_in_a.id),
                Task(room_id=room_a.id, title="t3", status="done", assignee_participant_id=bot_in_a.id),
                Task(room_id=room_a.id, title="t4", status="failed", assignee_participant_id=bot_in_a.id),
                # other agent's done — must not be touched when we sweep bot's done
                Task(room_id=room_b.id, title="t5", status="done", assignee_participant_id=other_in_b.id),
            ]
        )
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
            "bot": bot,
            "other": other,
        }
    await engine.dispose()


class TestBulkDeleteAgentTasks:
    @pytest.mark.asyncio
    async def test_admin_clears_done_for_agent(self, bulk_env):
        resp = await bulk_env["client"].request(
            "DELETE",
            f"/api/v1/agents/{bulk_env['bot'].id}/tasks",
            params={"status": "done"},
            headers=_auth(bulk_env["admin_token"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted_count": 2}

        # bot keeps todo and failed; the other agent's done is untouched.
        resp = await bulk_env["client"].get(
            f"/api/v1/agents/{bulk_env['bot'].id}/tasks",
            headers=_auth(bulk_env["admin_token"]),
        )
        assert sorted(r["title"] for r in resp.json()) == ["t1", "t4"]

        resp = await bulk_env["client"].get(
            f"/api/v1/agents/{bulk_env['other'].id}/tasks",
            headers=_auth(bulk_env["admin_token"]),
        )
        assert [r["title"] for r in resp.json()] == ["t5"]

    @pytest.mark.asyncio
    async def test_admin_clears_failed_for_agent(self, bulk_env):
        resp = await bulk_env["client"].request(
            "DELETE",
            f"/api/v1/agents/{bulk_env['bot'].id}/tasks",
            params={"status": "failed"},
            headers=_auth(bulk_env["admin_token"]),
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted_count": 1}

    @pytest.mark.asyncio
    async def test_non_admin_is_forbidden(self, bulk_env):
        resp = await bulk_env["client"].request(
            "DELETE",
            f"/api/v1/agents/{bulk_env['bot'].id}/tasks",
            params={"status": "done"},
            headers=_auth(bulk_env["regular_token"]),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_rejects_non_terminal_status(self, bulk_env):
        # Active states (todo / in_progress / blocked) are owned by the
        # agent runtime — clearing them via the admin sweep is the wrong
        # tool and is rejected at the boundary.
        for bad in ("todo", "in_progress", "blocked", "", "WHATEVER"):
            resp = await bulk_env["client"].request(
                "DELETE",
                f"/api/v1/agents/{bulk_env['bot'].id}/tasks",
                params={"status": bad},
                headers=_auth(bulk_env["admin_token"]),
            )
            assert resp.status_code == 400, (bad, resp.text)

    @pytest.mark.asyncio
    async def test_missing_status_param_is_rejected(self, bulk_env):
        resp = await bulk_env["client"].request(
            "DELETE",
            f"/api/v1/agents/{bulk_env['bot'].id}/tasks",
            headers=_auth(bulk_env["admin_token"]),
        )
        # Either 400 (we validate) or 422 (FastAPI required-query). Both
        # reject missing param, which is what callers must handle.
        assert resp.status_code in (400, 422)
