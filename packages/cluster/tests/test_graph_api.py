"""Tests for the /api/v1/graph topology endpoint (#58).

Covers permission matrix (admin / user / guest x global / personal / auto),
schema validation, ETag behavior, and — critically — personal-scope
leak prevention.
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

from doorae.app import create_app
from doorae.auth.jwt import create_guest_token, create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    User,
)


@pytest_asyncio.fixture()
async def graph_env():
    """Populate a small graph across two distinct owners.

    Layout:

        Project ``main`` (p1)
          Room ``#general`` (r1, alice is participant, bob NOT)
            parent_of →
              Room ``#general-help`` (r2, alice participant)
          Room ``#bobs-only`` (r3, bob participant, alice NOT)
            representative_agent = a2

        Project ``secret`` (p2)
          Room ``#secret`` (r4, bob participant, alice NOT)

        Users:
          admin (admin=True, owns m_admin)
          alice (owns m_alice)
          bob   (owns m_bob)

        Agents:
          a1 engine=codex, placed_on=m_alice, participates in r1
          a2 engine=claude, placed_on=m_bob, participates in r3 (rep.)
          a3 engine=gemini, placed_on=m_bob, participates in r4
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
        admin_user = User(email="admin@test.com", password_hash="x", is_admin=True)
        alice = User(email="alice@test.com", password_hash="x", is_admin=False)
        bob = User(email="bob@test.com", password_hash="x", is_admin=False)
        db.add_all([admin_user, alice, bob])
        await db.flush()

        m_admin = Machine(name="admin-mbp", hostname="h1", owner_user_id=admin_user.id)
        m_alice = Machine(name="alice-mbp", hostname="h2", owner_user_id=alice.id)
        m_bob = Machine(name="bob-mbp", hostname="h3", owner_user_id=bob.id)
        db.add_all([m_admin, m_alice, m_bob])
        await db.flush()

        p1 = Project(name="main")
        p2 = Project(name="secret")
        db.add_all([p1, p2])
        await db.flush()

        r1 = Room(project_id=p1.id, name="general")
        r3 = Room(project_id=p1.id, name="bobs-only")
        r4 = Room(project_id=p2.id, name="secret")
        db.add_all([r1, r3, r4])
        await db.flush()

        r2 = Room(project_id=p1.id, name="general-help", parent_room_id=r1.id)
        db.add(r2)
        await db.flush()

        a1 = Agent(name="alice-codex", engine="codex", placed_on_machine_id=m_alice.id)
        a2 = Agent(name="bob-claude", engine="claude", placed_on_machine_id=m_bob.id)
        a3 = Agent(name="bob-gemini", engine="gemini", placed_on_machine_id=m_bob.id)
        db.add_all([a1, a2, a3])
        await db.flush()

        # r3 representative agent = a2
        r3.representative_agent_id = a2.id
        await db.flush()

        parts: list[Participant] = [
            # alice participates in r1 + r2
            Participant(room_id=r1.id, user_id=alice.id, role="member"),
            Participant(room_id=r2.id, user_id=alice.id, role="member"),
            # bob participates in r3 + r4
            Participant(room_id=r3.id, user_id=bob.id, role="member"),
            Participant(room_id=r4.id, user_id=bob.id, role="member"),
            # agent participations
            Participant(room_id=r1.id, agent_id=a1.id, role="member"),
            Participant(room_id=r3.id, agent_id=a2.id, role="member"),
            Participant(room_id=r4.id, agent_id=a3.id, role="member"),
        ]
        db.add_all(parts)
        await db.commit()

        # Refresh to capture auto-generated ids
        ids = {
            "admin_user_id": admin_user.id,
            "alice_id": alice.id,
            "bob_id": bob.id,
            "m_admin": m_admin.id,
            "m_alice": m_alice.id,
            "m_bob": m_bob.id,
            "p1": p1.id,
            "p2": p2.id,
            "r1": r1.id,
            "r2": r2.id,
            "r3": r3.id,
            "r4": r4.id,
            "a1": a1.id,
            "a2": a2.id,
            "a3": a3.id,
        }

    admin_token = create_user_token(
        ids["admin_user_id"], "admin@test.com", True, secret=config.jwt_secret
    )
    alice_token = create_user_token(
        ids["alice_id"], "alice@test.com", False, secret=config.jwt_secret
    )
    bob_token = create_user_token(
        ids["bob_id"], "bob@test.com", False, secret=config.jwt_secret
    )

    # Build a guest token attached to r1 through an invite row.
    async with factory() as db:
        invite = RoomInviteLink(
            room_id=ids["r1"],
            created_by_user_id=ids["admin_user_id"],
            token_hash="xx" * 32,
            lookup_hint="hint" + "0" * 8,
        )
        db.add(invite)
        await db.flush()
        invite_id = invite.id
        guest_user = User(is_anonymous=True, display_name="guest")
        db.add(guest_user)
        await db.flush()
        guest_id = guest_user.id
        await db.commit()

    from datetime import datetime, timedelta, timezone as dt_tz
    guest_token = create_guest_token(
        user_id=guest_id,
        room_id=ids["r1"],
        invite_id=invite_id,
        display_name="guest",
        secret=config.jwt_secret,
        expires_at=datetime.now(dt_tz.utc) + timedelta(hours=1),
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "admin_token": admin_token,
            "alice_token": alice_token,
            "bob_token": bob_token,
            "guest_token": guest_token,
            "ids": ids,
            "factory": factory,
        }

    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestGraphPermissions:
    @pytest.mark.asyncio
    async def test_admin_auto_returns_global(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get("/api/v1/graph", headers=_auth(graph_env["admin_token"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "global"

    @pytest.mark.asyncio
    async def test_user_auto_returns_personal(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get("/api/v1/graph", headers=_auth(graph_env["alice_token"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "personal"

    @pytest.mark.asyncio
    async def test_user_global_forbidden(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get(
            "/api/v1/graph?scope=global",
            headers=_auth(graph_env["alice_token"]),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_ask_personal(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get(
            "/api/v1/graph?scope=personal",
            headers=_auth(graph_env["admin_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "personal"

    @pytest.mark.asyncio
    async def test_guest_forbidden(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get("/api/v1/graph", headers=_auth(graph_env["guest_token"]))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_auth_unauthorized(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get("/api/v1/graph")
        assert resp.status_code == 401


class TestGraphSchema:
    @pytest.mark.asyncio
    async def test_global_graph_schema(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get(
            "/api/v1/graph?scope=global",
            headers=_auth(graph_env["admin_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "generated_at" in body
        assert "nodes" in body and isinstance(body["nodes"], list)
        assert "edges" in body and isinstance(body["edges"], list)

        valid_kinds = {"user", "machine", "agent", "room", "project"}
        for node in body["nodes"]:
            assert set(node.keys()) >= {"id", "kind", "label", "data"}
            assert node["kind"] in valid_kinds
            # Prefixes enforce uniqueness between tables sharing uuids
            prefix = node["id"][0:2]
            assert prefix in {"u_", "m_", "a_", "r_", "p_"}

        valid_edge_kinds = {
            "owns",
            "places",
            "participates",
            "parent_of",
            "represents",
        }
        for edge in body["edges"]:
            assert set(edge.keys()) >= {"id", "source", "target", "kind"}
            assert edge["kind"] in valid_edge_kinds

    @pytest.mark.asyncio
    async def test_global_contains_all_owners(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get(
            "/api/v1/graph?scope=global",
            headers=_auth(graph_env["admin_token"]),
        )
        body = resp.json()
        ids = graph_env["ids"]
        node_ids = {n["id"] for n in body["nodes"]}
        assert f"u_{ids['alice_id']}" in node_ids
        assert f"u_{ids['bob_id']}" in node_ids
        assert f"m_{ids['m_alice']}" in node_ids
        assert f"m_{ids['m_bob']}" in node_ids
        assert f"a_{ids['a1']}" in node_ids
        assert f"a_{ids['a2']}" in node_ids
        assert f"a_{ids['a3']}" in node_ids

        # Edge presence checks
        edges = body["edges"]
        owns_pairs = {(e["source"], e["target"]) for e in edges if e["kind"] == "owns"}
        assert (f"u_{ids['alice_id']}", f"m_{ids['m_alice']}") in owns_pairs
        places_pairs = {(e["source"], e["target"]) for e in edges if e["kind"] == "places"}
        assert (f"m_{ids['m_alice']}", f"a_{ids['a1']}") in places_pairs

        represents_pairs = {(e["source"], e["target"]) for e in edges if e["kind"] == "represents"}
        assert (f"r_{ids['r3']}", f"a_{ids['a2']}") in represents_pairs

        parent_pairs = {(e["source"], e["target"]) for e in edges if e["kind"] == "parent_of"}
        assert (f"r_{ids['r1']}", f"r_{ids['r2']}") in parent_pairs


class TestPersonalScopeLeakPrevention:
    @pytest.mark.asyncio
    async def test_alice_personal_excludes_bob(self, graph_env) -> None:
        """Critical security invariant: no other-user ids leak via the
        personal scope — not as user nodes, not as machine nodes,
        not as agents-on-their-machines, not as rooms they're in.
        """
        client = graph_env["client"]
        ids = graph_env["ids"]
        resp = await client.get(
            "/api/v1/graph?scope=personal",
            headers=_auth(graph_env["alice_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        node_ids = {n["id"] for n in body["nodes"]}

        # Alice's slice must include her own identity + m_alice + a1 + r1 + r2.
        assert f"u_{ids['alice_id']}" in node_ids
        assert f"m_{ids['m_alice']}" in node_ids
        assert f"a_{ids['a1']}" in node_ids
        assert f"r_{ids['r1']}" in node_ids
        assert f"r_{ids['r2']}" in node_ids

        # Bob and his resources must NOT appear.
        assert f"u_{ids['bob_id']}" not in node_ids
        assert f"m_{ids['m_bob']}" not in node_ids
        assert f"a_{ids['a2']}" not in node_ids
        assert f"a_{ids['a3']}" not in node_ids
        assert f"r_{ids['r3']}" not in node_ids
        assert f"r_{ids['r4']}" not in node_ids
        assert f"u_{ids['admin_user_id']}" not in node_ids

        # No edges may reference bob-owned ids either.
        edges = body["edges"]
        for e in edges:
            assert f"_{ids['bob_id']}" not in e["source"]
            assert f"_{ids['bob_id']}" not in e["target"]
            assert f"_{ids['m_bob']}" not in e["source"]
            assert f"_{ids['m_bob']}" not in e["target"]

    @pytest.mark.asyncio
    async def test_alice_sees_foreign_agent_in_her_room_with_nulled_placement(
        self, graph_env
    ) -> None:
        """When a foreign-owned agent co-participates in a user's room
        the agent node is visible (alice needs to know who is in her
        room) but the agent's placement machine id MUST be nulled —
        otherwise the foreign owner (bob) leaks through the backdoor.
        """
        ids = graph_env["ids"]
        factory = graph_env["factory"]
        # Join bob's agent a2 into alice's room r1.
        async with factory() as db:
            db.add(
                Participant(room_id=ids["r1"], agent_id=ids["a2"], role="member")
            )
            await db.commit()

        client = graph_env["client"]
        resp = await client.get(
            "/api/v1/graph?scope=personal",
            headers=_auth(graph_env["alice_token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        nodes_by_id = {n["id"]: n for n in body["nodes"]}

        # a2 IS visible (alice must see who is in her room)
        a2_node = nodes_by_id.get(f"a_{ids['a2']}")
        assert a2_node is not None, (
            "foreign agent co-participating in alice's room must be visible"
        )
        # But its placement machine MUST be hidden.
        placed_on = a2_node["data"].get("placed_on_machine_id")
        assert placed_on in (None, ""), (
            f"a2.placed_on_machine_id leaked bob's machine as {placed_on!r}"
        )

        # m_bob and bob still must not appear as nodes.
        assert f"m_{ids['m_bob']}" not in nodes_by_id
        assert f"u_{ids['bob_id']}" not in nodes_by_id

        # No edges touching m_bob or bob ids.
        for e in body["edges"]:
            assert f"_{ids['m_bob']}" not in e["source"]
            assert f"_{ids['m_bob']}" not in e["target"]
            assert f"_{ids['bob_id']}" not in e["source"]
            assert f"_{ids['bob_id']}" not in e["target"]


class TestEtag:
    @pytest.mark.asyncio
    async def test_etag_roundtrip_returns_304(self, graph_env) -> None:
        client = graph_env["client"]
        resp1 = await client.get(
            "/api/v1/graph?scope=global",
            headers=_auth(graph_env["admin_token"]),
        )
        assert resp1.status_code == 200
        etag = resp1.headers.get("etag")
        assert etag, "ETag header must be present"

        resp2 = await client.get(
            "/api/v1/graph?scope=global",
            headers={**_auth(graph_env["admin_token"]), "If-None-Match": etag},
        )
        assert resp2.status_code == 304

    @pytest.mark.asyncio
    async def test_cache_control_private(self, graph_env) -> None:
        client = graph_env["client"]
        resp = await client.get(
            "/api/v1/graph?scope=global",
            headers=_auth(graph_env["admin_token"]),
        )
        cache_ctl = resp.headers.get("cache-control", "")
        assert "private" in cache_ctl
        assert "max-age" in cache_ctl


class TestNPlusOneGuard:
    @pytest.mark.asyncio
    async def test_global_query_count_bounded(self, graph_env) -> None:
        """Ensure the endpoint does not blow up on query count (N+1).

        The endpoint should execute a bounded number of SELECTs
        regardless of graph size. We budget 12 — generous for initial
        implementation, but catches accidental per-row fetches which
        would quickly exceed it as the seed data grows.
        """
        factory = graph_env["factory"]
        async with factory() as sess:
            bind = sess.get_bind()
        # AsyncSession.get_bind() returns the *sync* Engine the
        # AsyncEngine wraps (SQLAlchemy 2.0 behaviour). Register the
        # listener on that sync engine.
        sync_engine = bind

        count = 0

        def _count_selects(conn, cursor, statement, *args, **kwargs):  # noqa: ANN001
            nonlocal count
            if statement.lstrip().lower().startswith("select"):
                count += 1

        event.listen(sync_engine, "before_cursor_execute", _count_selects)
        try:
            client = graph_env["client"]
            resp = await client.get(
                "/api/v1/graph?scope=global",
                headers=_auth(graph_env["admin_token"]),
            )
            assert resp.status_code == 200
        finally:
            event.remove(sync_engine, "before_cursor_execute", _count_selects)

        assert count <= 12, f"global graph fetch issued {count} SELECTs (>12 budget)"
