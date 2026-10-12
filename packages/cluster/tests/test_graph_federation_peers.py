"""FED-09 — federation peers appear in the topology graph.

Peers are cluster-wide trust records managed only by admins, so they
are surfaced in the ``global`` scope only. A peer node carries its
trust state (never the certificate, fingerprint or endpoint) and a
``federates`` edge to every local room it holds an active, unexpired
shared-channel grant on.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Participant, Project, Room, User
from anygarden.federation.models import Peer, PeerGrant

LOCAL_NODE = "00000000-0000-0000-0000-00000000aaaa"
PEER_ACTIVE = "11111111-1111-1111-1111-111111111111"
PEER_REVOKED = "22222222-2222-2222-2222-222222222222"


@pytest_asyncio.fixture()
async def peer_env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(UTC)
    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        alice = User(email="alice@test.com", password_hash="x", is_admin=False)
        db.add_all([admin, alice])
        await db.flush()
        project = Project(name="main")
        db.add(project)
        await db.flush()
        shared = Room(project_id=project.id, name="shared")
        other = Room(project_id=project.id, name="other")
        expired_room = Room(project_id=project.id, name="expired")
        db.add_all([shared, other, expired_room])
        await db.flush()
        db.add(Participant(room_id=shared.id, user_id=alice.id, role="member"))

        def _peer(node_id: str, state: str) -> Peer:
            return Peer(
                node_id=node_id,
                certificate_pem="-----BEGIN CERTIFICATE-----SECRET-PEM",
                fingerprint="f" * 64,
                endpoint={"host": "10.0.0.9", "port": 7443},
                state=state,
                epoch=3,
                approved_by=admin.id,
                updated_at=now,
            )

        def _grant(peer: str, channel: str, *, active=True, expires=None) -> PeerGrant:
            return PeerGrant(
                peer_node_id=peer,
                authority_node_id=LOCAL_NODE,
                channel_id=channel,
                epoch=1,
                active=active,
                actors=[],
                capabilities=["read", "write"],
                role="member",
                expires_at=expires or now + timedelta(days=1),
                approved_by=admin.id,
            )

        db.add_all([_peer(PEER_ACTIVE, "active"), _peer(PEER_REVOKED, "revoked")])
        db.add_all(
            [
                _grant(PEER_ACTIVE, shared.id),
                # inactive grant → no edge
                _grant(PEER_ACTIVE, other.id, active=False),
                # expired grant → no edge
                _grant(PEER_ACTIVE, expired_room.id, expires=now - timedelta(hours=1)),
                # grant on a channel that isn't a local room → no dangling edge
                _grant(PEER_ACTIVE, "99999999-9999-9999-9999-999999999999"),
                # revoked peer keeps no live edges even if a row says active
                _grant(PEER_REVOKED, shared.id),
            ]
        )
        await db.commit()
        ids = {
            "admin": admin.id,
            "alice": alice.id,
            "shared": shared.id,
            "other": other.id,
            "expired": expired_room.id,
        }

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    tokens = {
        "admin": create_user_token(ids["admin"], "admin@test.com", True, secret=config.jwt_secret),
        "alice": create_user_token(ids["alice"], "alice@test.com", False, secret=config.jwt_secret),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "tokens": tokens, "ids": ids}
    await engine.dispose()


async def _graph(env, who: str, scope: str = "auto") -> dict:
    resp = await env["client"].get(
        f"/api/v1/graph?scope={scope}",
        headers={"Authorization": f"Bearer {env['tokens'][who]}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.req("FED-09")
@pytest.mark.asyncio
async def test_admin_global_graph_includes_peers_with_state(peer_env) -> None:
    body = await _graph(peer_env, "admin")
    peers = {n["id"]: n for n in body["nodes"] if n["kind"] == "peer"}
    assert set(peers) == {f"f_{PEER_ACTIVE}", f"f_{PEER_REVOKED}"}

    active = peers[f"f_{PEER_ACTIVE}"]
    assert active["label"] == PEER_ACTIVE
    assert active["data"]["node_id"] == PEER_ACTIVE
    assert active["data"]["state"] == "active"
    assert active["data"]["certificate_epoch"] == 3
    assert active["data"]["state_changed_at"]
    assert active["data"]["channel_count"] == 1
    assert peers[f"f_{PEER_REVOKED}"]["data"]["state"] == "revoked"


@pytest.mark.req("FED-09")
@pytest.mark.asyncio
async def test_peer_edges_only_for_active_grants_on_local_rooms(peer_env) -> None:
    body = await _graph(peer_env, "admin")
    ids = peer_env["ids"]
    fed = [e for e in body["edges"] if e["kind"] == "federates"]
    assert [(e["source"], e["target"]) for e in fed] == [
        (f"f_{PEER_ACTIVE}", f"r_{ids['shared']}")
    ]
    assert fed[0]["data"]["role"] == "member"
    node_ids = {n["id"] for n in body["nodes"]}
    for e in body["edges"]:
        assert e["source"] in node_ids and e["target"] in node_ids


@pytest.mark.req("FED-09")
@pytest.mark.asyncio
async def test_revoked_peer_has_no_edges(peer_env) -> None:
    body = await _graph(peer_env, "admin")
    assert not [e for e in body["edges"] if e["source"] == f"f_{PEER_REVOKED}"]
    revoked = next(n for n in body["nodes"] if n["id"] == f"f_{PEER_REVOKED}")
    assert revoked["data"]["channel_count"] == 0


@pytest.mark.req("FED-09")
@pytest.mark.asyncio
async def test_peer_node_never_exposes_credentials(peer_env) -> None:
    body = await _graph(peer_env, "admin")
    blob = str([n for n in body["nodes"] if n["kind"] == "peer"])
    assert "SECRET-PEM" not in blob
    assert "f" * 64 not in blob
    assert "10.0.0.9" not in blob
    for n in body["nodes"]:
        if n["kind"] == "peer":
            assert not {"certificate_pem", "fingerprint", "endpoint"} & set(n["data"])


@pytest.mark.req("FED-09")
@pytest.mark.asyncio
@pytest.mark.parametrize(("who", "scope"), [("alice", "auto"), ("admin", "personal")])
async def test_personal_scope_excludes_peers(peer_env, who: str, scope: str) -> None:
    body = await _graph(peer_env, who, scope)
    assert body["scope"] == "personal"
    assert not [n for n in body["nodes"] if n["kind"] == "peer"]
    assert not [e for e in body["edges"] if e["kind"] == "federates"]
