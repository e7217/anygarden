"""#839 — admin APIs that turn connection cards and invite codes into peering."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from anygarden.auth.dependencies import Identity
from anygarden.db.models import User
from anygarden.dependencies import get_admin_identity
from anygarden.federation.connection_codes import (
    decode_card,
    decode_invite,
    encode_card,
)
from anygarden.federation.models import Peer
from anygarden.federation.router import mount_admin
from anygarden.federation.transport import PeerListener

from .test_federation_trust import pair  # noqa: F401


@asynccontextmanager
async def admin_client(node, *, public_url=None):
    app = FastAPI()
    if public_url is not None:
        app.state.config = SimpleNamespace(peer_public_url=public_url)
    mount_admin(app, node.s)
    app.dependency_overrides[get_admin_identity] = lambda: Identity(
        kind="user", id=node.admin
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def scope_for(a, b):
    return [
        {
            "channel_id": a.channel,
            "actors": [
                {"node_id": b.s.node_id, "kind": "agent", "principal_id": b.actor}
            ],
            "capabilities": ["channel.read", "message.send"],
            "role": "member",
        }
    ]


async def test_identity_reports_this_node_and_public_url(pair):
    a, _ = pair
    async with admin_client(a, public_url="https://alpha.example:8451") as client:
        r = await client.get("/api/v1/node/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == a.s.node_id
    assert body["fingerprint"] == a.s.identity.fingerprint
    assert body["public_url"] == "https://alpha.example:8451"
    assert body["certificate_expires_at"]


async def test_card_defaults_to_the_configured_public_url(pair):
    a, _ = pair
    async with admin_client(a, public_url="https://alpha.example:8451") as client:
        r = await client.post("/api/v1/node/connection-card", json={})
        override = await client.post(
            "/api/v1/node/connection-card", json={"url": "https://10.0.0.5:9000"}
        )
    assert r.status_code == 200
    card, identity = decode_card(r.json()["card"])
    assert card.url == "https://alpha.example:8451"
    assert identity.fingerprint == r.json()["fingerprint"] == a.s.identity.fingerprint
    assert decode_card(override.json()["card"])[0].url == "https://10.0.0.5:9000"


async def test_card_needs_a_valid_url(pair):
    a, _ = pair
    async with admin_client(a) as client:
        missing = await client.post("/api/v1/node/connection-card", json={})
        bad = await client.post(
            "/api/v1/node/connection-card", json={"url": "http://alpha:8451"}
        )
    assert missing.status_code == 400
    assert missing.json() == {"code": "PUBLIC_URL_REQUIRED"}
    assert bad.status_code == 400
    assert bad.json() == {"code": "PUBLIC_URL_INVALID"}


@pytest.mark.req("FED-11")
async def test_card_inspection_previews_identity_and_private_ips(pair):
    a, b = pair
    card = encode_card(b.s.node_id, b.s.certificate_pem, "https://192.168.7.20:8451")
    async with admin_client(a) as client:
        r = await client.post(
            "/api/v1/node/connection-card/inspect", json={"card": card}
        )
        bad = await client.post(
            "/api/v1/node/connection-card/inspect", json={"card": "agpeer1.%%%"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == b.s.node_id
    assert body["fingerprint"] == b.s.identity.fingerprint
    assert body["certificate_pem"] == b.s.certificate_pem
    assert body["url"] == "https://192.168.7.20:8451"
    assert body["suggested_ips"] == ["192.168.7.20"]
    assert body["requires_private"] is True
    assert bad.status_code == 400 and bad.json() == {"code": "CODE_INVALID"}


async def test_inspection_rejects_this_nodes_own_card(pair):
    a, _ = pair
    own = encode_card(a.s.node_id, a.s.certificate_pem, "https://a.example:8451")
    async with admin_client(a) as client:
        r = await client.post(
            "/api/v1/node/connection-card/inspect", json={"card": own}
        )
    assert r.status_code == 400 and r.json() == {"code": "IDENTITY_MISMATCH"}


async def test_connection_routes_are_admin_only(pair):
    a, b = pair
    card = encode_card(b.s.node_id, b.s.certificate_pem, "https://b.example:8451")
    async with admin_client(a, public_url="https://a.example:8451") as client:
        async with a.s.sessions.begin() as db:
            await db.execute(update(User).values(is_admin=False))
        for method, path, body in (
            ("get", "/api/v1/node/identity", None),
            ("post", "/api/v1/node/connection-card", {}),
            ("post", "/api/v1/node/connection-card/inspect", {"card": card}),
            ("post", "/api/v1/node/invite-code/inspect", {"code": "aginvite1.x"}),
        ):
            kwargs = {} if body is None else {"json": body}
            r = await getattr(client, method)(path, **kwargs)
            assert r.status_code == 403, path


@pytest.mark.req("FED-11")
async def test_card_and_invite_code_alone_complete_real_mtls_peering(pair):
    a, b = pair
    listener_a = await PeerListener(a.s).start()
    issuer_url = f"https://127.0.0.1:{listener_a.port}"
    try:
        async with admin_client(b, public_url="https://127.0.0.1:8443") as client_b:
            card = (
                await client_b.post("/api/v1/node/connection-card", json={})
            ).json()["card"]
        async with admin_client(a, public_url=issuer_url) as client_a:
            seen = (
                await client_a.post(
                    "/api/v1/node/connection-card/inspect", json={"card": card}
                )
            ).json()
            created = await client_a.post(
                "/api/v1/node/invites",
                json={
                    "intended_node_id": seen["node_id"],
                    "certificate_pem": seen["certificate_pem"],
                    "endpoint": {
                        "url": seen["url"],
                        "approved_ips": seen["suggested_ips"],
                    },
                    "scopes": scope_for(a, b),
                },
            )
        assert created.status_code == 201, created.text
        code = created.json()["invite_code"]

        async with admin_client(b) as client_b:
            preview = await client_b.post(
                "/api/v1/node/invite-code/inspect", json={"code": code}
            )
            assert preview.status_code == 200, preview.text
            assert preview.headers["cache-control"] == "no-store"
            p = preview.json()
            assert p["issuer_node_id"] == a.s.node_id
            assert p["issuer_fingerprint"] == a.s.identity.fingerprint
            assert p["issuer_url"] == issuer_url
            assert p["suggested_ips"] == ["127.0.0.1"]
            accepted = await client_b.post(
                f"/api/v1/node/invites/{p['bundle']['invite_id']}/accept",
                json={
                    "bundle": p["bundle"],
                    "issuer_endpoint": {
                        "url": p["issuer_url"],
                        "approved_ips": p["suggested_ips"],
                    },
                },
            )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["state"] == "accepted"
        async with b.s.sessions() as db:
            peer = (await db.execute(select(Peer))).scalar_one()
        assert peer.node_id == a.s.node_id and peer.state == "active"
    finally:
        await listener_a.stop()


async def test_invite_uses_explicit_issuer_url_over_configured_one(pair):
    a, b = pair
    async with admin_client(a, public_url="https://a.example:8451") as client:
        r = await client.post(
            "/api/v1/node/invites",
            json={
                "intended_node_id": b.s.node_id,
                "certificate_pem": b.s.certificate_pem,
                "endpoint": {
                    "url": "https://127.0.0.1:8443",
                    "approved_ips": ["127.0.0.1"],
                },
                "scopes": scope_for(a, b),
                "issuer_url": "https://203.0.113.9:8451",
            },
        )
        own = await client.post(
            "/api/v1/node/invite-code/inspect", json={"code": r.json()["invite_code"]}
        )
    assert r.status_code == 201, r.text
    code, _ = decode_invite(r.json()["invite_code"])
    assert code.issuer_url == "https://203.0.113.9:8451"
    # This node cannot accept its own invitation.
    assert own.status_code == 400 and own.json() == {"code": "IDENTITY_MISMATCH"}


async def test_invite_without_any_issuer_url_still_returns_the_bundle(pair):
    a, b = pair
    async with admin_client(a) as client:
        r = await client.post(
            "/api/v1/node/invites",
            json={
                "intended_node_id": b.s.node_id,
                "certificate_pem": b.s.certificate_pem,
                "endpoint": {
                    "url": "https://127.0.0.1:8443",
                    "approved_ips": ["127.0.0.1"],
                },
                "scopes": scope_for(a, b),
            },
        )
    assert r.status_code == 201, r.text
    assert r.json()["invite_code"] is None
    assert len(r.json()["token"]) > 32


async def test_identity_reports_the_pin_kind_and_leaf_expiry(pair):
    a, _ = pair
    async with admin_client(a) as client:
        body = (await client.get("/api/v1/node/identity")).json()
    if a.s.identity.root:
        assert body["pin_kind"] == "root"
        assert body["leaf_expires_at"] < body["certificate_expires_at"]
    else:
        assert body["pin_kind"] == "certificate"
        assert body["leaf_expires_at"] is None
