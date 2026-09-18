"""Product trust tests: separate SQL databases and real loopback-only TLS sockets."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Room, User
from anygarden.federation.certificates import (
    create_credentials,
    inspect_certificate,
)
from anygarden.federation.endpoint import validate_endpoint
from anygarden.federation.errors import PeerError
from anygarden.federation.models import (
    Peer,
    PeerAcceptance,
    PeerAudit,
    PeerConsent,
    PeerControlEvent,
    PeerGrant,
    PeerInvite,
)
from anygarden.federation.router import create_peer_app
from anygarden.federation.schemas import (
    Endpoint,
    InviteAccept,
    InviteCreate,
    Principal,
    Scope,
)
from anygarden.federation.service import PeerService, now
from anygarden.federation.transport import PeerListener, post
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select, update


def uid():
    return str(uuid4())


def endpoint(port=8443):
    return Endpoint(url=f"https://127.0.0.1:{port}", approved_ips=["127.0.0.1"])


@pytest.fixture
async def pair(tmp_path):
    nodes = []
    for label in ("a", "b"):
        node, admin, channel, actor = uid(), uid(), uid(), uid()
        cert, key = create_credentials(tmp_path / label, node)
        engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / (label + '.db')}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = build_session_factory(engine)
        async with sessions.begin() as db:
            db.add(
                User(
                    id=admin,
                    email=f"{label}@example.test",
                    password_hash="synthetic",
                    is_admin=True,
                )
            )
            db.add(Room(id=channel, name=label))
        service = PeerService(
            node_id=node,
            cert_path=cert,
            key_path=key,
            sessions=sessions,
            allow_loopback=True,
        )
        nodes.append(
            SimpleNamespace(
                s=service, admin=admin, channel=channel, actor=actor, engine=engine
            )
        )
    yield nodes
    for n in nodes:
        await n.engine.dispose()


def invitation(a, b, **kwargs):
    return InviteCreate(
        intended_node_id=b.s.node_id,
        certificate_pem=b.s.certificate_pem,
        endpoint=endpoint(),
        scopes=[
            Scope(
                channel_id=a.channel,
                actors=[
                    Principal(node_id=b.s.node_id, kind="agent", principal_id=b.actor)
                ],
                capabilities=["channel.read", "message.send", "task.execute"],
            )
        ],
        **kwargs,
    )


async def admit(a, b):
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    receipt = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    accept = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, accept)
    await b.s.finish_accept(b.admin, accept, receipt)
    return bundle, receipt


async def authorize(a, b, *, action="message.send", epoch=1, **kw):
    async with a.s.sessions.begin() as db:
        return await a.s.authorize(
            db,
            b.s.identity,
            sender_node_id=b.s.node_id,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            principal=Principal(
                node_id=b.s.node_id, kind="agent", principal_id=b.actor
            ),
            action=action,
            grant_epoch=epoch,
            **kw,
        )


async def count(s, model):
    async with s.sessions() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_atomic_redeem_concurrent_one_grant_and_durable_duplicate(pair):
    a, b = pair
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    args = (
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    receipts = await asyncio.gather(*(a.s.redeem(*args) for _ in range(8)))
    assert all(r == receipts[0] for r in receipts)
    assert await count(a.s, PeerGrant) == 1
    async with a.s.sessions() as db:
        audits = await db.scalar(
            select(func.count())
            .select_from(PeerAudit)
            .where(PeerAudit.action == "invite.redeem")
        )
        assert audits == 1
        assert (
            await db.get(PeerGrant, (b.s.node_id, a.s.node_id, a.channel))
        ).epoch == 1
    # Persistence, not an in-memory dedup cache.
    restarted = PeerService(
        node_id=a.s.node_id,
        cert_path=a.s.cert_path,
        key_path=a.s.key_path,
        sessions=a.s.sessions,
        allow_loopback=True,
    )
    assert await restarted.redeem(*args) == receipts[0]


async def test_ack_loss_keeps_local_pending_recover_same_receipt(pair):
    a, b = pair
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    body = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    assert await b.s.begin_accept(b.admin, body) is None
    receipt = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    async with b.s.sessions() as db:
        assert (await db.get(PeerAcceptance, str(bundle.invite_id))).state == "pending"
        assert (await db.get(Peer, a.s.node_id)).state == "pending"
    assert await b.s.begin_accept(b.admin, body) is None
    replay = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    assert replay == receipt
    await b.s.finish_accept(b.admin, body, replay)
    assert await b.s.begin_accept(b.admin, body) == receipt
    assert (
        await count(b.s, PeerGrant) == 1
    )  # authority-issued mirror grant only, no local exposure


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("expired", "INVITE_CONSUMED_OR_EXPIRED"),
        ("token", "INVITE_DENIED"),
        ("sender", "IDENTITY_MISMATCH"),
    ],
)
async def test_invalid_invite(pair, mutation, code):
    a, b = pair
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    token, sender = bundle.token.get_secret_value(), b.s.node_id
    if mutation == "expired":
        async with a.s.sessions.begin() as db:
            await db.execute(
                update(PeerInvite).values(expires_at=now() - timedelta(seconds=1))
            )
        # No general admission; bootstrap itself correctly fails closed.
        code = "BOOTSTRAP_DENIED"
    elif mutation == "token":
        token = "wrong" * 8
    else:
        sender = uid()
    with pytest.raises(PeerError, match=code):
        await a.s.redeem(b.s.identity, sender, str(bundle.invite_id), token)
    assert await count(a.s, PeerGrant) == 0


async def test_revoke_durable_before_delivery_and_reinvite_does_not_restore_old_grant(
    pair,
):
    a, b = pair
    bundle, _ = await admit(a, b)
    await authorize(a, b)
    await a.s.revoke(a.admin, b.s.node_id)
    assert await count(a.s, PeerControlEvent) == 1
    with pytest.raises(PeerError):
        await authorize(a, b)
    with pytest.raises(PeerError):
        await a.s.redeem(
            b.s.identity,
            b.s.node_id,
            str(bundle.invite_id),
            bundle.token.get_secret_value(),
        )
    new = await a.s.create_invite(a.admin, invitation(a, b))
    receipt = await a.s.redeem(
        b.s.identity, b.s.node_id, str(new.invite_id), new.token.get_secret_value()
    )
    assert receipt["grants"][0]["grant_epoch"] == 3
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await authorize(a, b, epoch=1)
    await authorize(a, b, epoch=3)


async def test_fresh_authorization_before_cached_receipt(pair):
    a, b = pair
    bundle, _ = await admit(a, b)
    async with a.s.sessions.begin() as db:
        await db.execute(update(PeerGrant).values(active=False, epoch=2))
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await a.s.redeem(
            b.s.identity,
            b.s.node_id,
            str(bundle.invite_id),
            bundle.token.get_secret_value(),
        )


async def test_scope_principal_policy_archive_and_demoted_admin(pair):
    a, b = pair
    await admit(a, b)
    with pytest.raises(PeerError, match="SCOPE_DENIED"):
        await authorize(a, b, action="file.read")
    with pytest.raises(PeerError, match="SCOPE_DENIED"):
        await authorize(a, b, action="admin")
    with pytest.raises(PeerError, match="LOCAL_POLICY_DENIED"):
        await authorize(a, b, action="task.execute")
    with pytest.raises(PeerError, match="LOCAL_POLICY_DENIED"):
        await authorize(a, b, policy_epoch=999)
    actor = b.actor
    b.actor = uid()
    with pytest.raises(PeerError, match="PRINCIPAL_DENIED"):
        await authorize(a, b)
    b.actor = actor
    channel = a.channel
    a.channel = uid()
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await authorize(a, b)
    a.channel = channel
    async with a.s.sessions.begin() as db:
        await db.execute(update(Room).values(archived_at=now()))
    with pytest.raises(PeerError, match="CHANNEL_DENIED"):
        await authorize(a, b)
    async with a.s.sessions.begin() as db:
        await db.execute(update(Room).values(archived_at=None))
        await db.execute(update(User).values(is_admin=False))
    with pytest.raises(PeerError, match="ADMIN_REQUIRED"):
        await authorize(a, b)


async def test_pin_rotation_invalidates_old_connection_and_never_restores_grants(
    pair, tmp_path
):
    a, b = pair
    await admit(a, b)
    cert, _key = create_credentials(tmp_path / "new-b", b.s.node_id)
    pem = cert.read_text()
    old_fingerprint = b.s.identity.fingerprint
    result = await a.s.rotate(a.admin, b.s.node_id, pem, 1)
    assert result["peer_epoch"] == 2
    assert result["fingerprint"] != old_fingerprint
    with pytest.raises(PeerError, match="PIN_MISMATCH"):
        await authorize(a, b)
    b.s.identity = inspect_certificate(pem)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await authorize(a, b)
    with pytest.raises(PeerError, match="PEER_EPOCH_CONFLICT"):
        await a.s.rotate(a.admin, b.s.node_id, b.s.certificate_pem, 1)


async def test_revoke_control_only_and_replay_body_conflict(pair):
    a, b = pair
    await admit(a, b)
    await a.s.revoke(a.admin, b.s.node_id)
    await b.s.revoke(b.admin, a.s.node_id)
    event = uid()
    ack = await b.s.receive_revoke(a.s.identity, a.s.node_id, event, 2)
    assert await b.s.receive_revoke(a.s.identity, a.s.node_id, event, 2) == ack
    with pytest.raises(PeerError, match="ID_CONFLICT"):
        await b.s.receive_revoke(a.s.identity, a.s.node_id, event, 3)
    with pytest.raises(PeerError):
        await b.s.hello(a.s.identity, a.s.node_id, 1)


@pytest.mark.parametrize(
    "url,ips,private",
    [
        ("http://peer.test", ["8.8.8.8"], False),
        ("https://user:secret@peer.test", ["8.8.8.8"], False),
        ("https://peer.test/path", ["8.8.8.8"], False),
        ("https://peer.test#fragment", ["8.8.8.8"], False),
        ("https://peer.test", ["169.254.169.254"], True),
        ("https://peer.test", ["100.100.100.200"], True),
        ("https://peer.test", ["168.63.129.16"], True),
        ("https://peer.test", ["::ffff:127.0.0.1"], True),
        ("https://peer.test", ["127.0.0.1"], True),
        ("https://peer.test", ["0.0.0.0"], True),
        ("https://peer.test", ["10.0.0.1"], False),
        ("https://8.8.8.8", ["1.1.1.1"], False),
        ("https://peer.test:0", ["8.8.8.8"], False),
    ],
)
def test_ssrf_denials(url, ips, private):
    with pytest.raises(PeerError, match="ENDPOINT_DENIED"):
        validate_endpoint(Endpoint(url=url, approved_ips=ips, allow_private=private))


def test_exact_admin_addresses_only_no_dns_resolution():
    value = validate_endpoint(
        Endpoint(
            url="https://node.example.test:8443",
            approved_ips=["10.1.2.3"],
            allow_private=True,
        )
    )
    assert value.ips == ("10.1.2.3",)
    assert value.host == "node.example.test"


async def test_plaintext_headers_cannot_impersonate_mtls_and_no_admin_routes(pair):
    a, b = pair
    await admit(a, b)
    app = create_peer_app(a.s)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/api/v1/federation/hello",
            headers={
                "x-peer-node-id": b.s.node_id,
                "x-client-cert": b.s.certificate_pem.replace("\n", " "),
            },
            json={"sender_node_id": b.s.node_id, "protocol_version": 1},
        )
        assert r.status_code == 401
        assert (await c.get("/api/v1/node/peers")).status_code == 404
        assert (await c.get("/api/v1/rooms")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404


@asynccontextmanager
async def live_pair(a, b):
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    listener_a = await PeerListener(a.s).start()
    acceptance = InviteAccept(bundle=bundle, issuer_endpoint=endpoint(listener_a.port))
    await b.s.begin_accept(b.admin, acceptance)
    listener_b = await PeerListener(b.s).start()
    try:
        yield bundle, acceptance, listener_a, listener_b
    finally:
        await listener_b.stop()
        await listener_a.stop()


async def test_two_independent_nodes_real_tls_bootstrap_redeem_and_identity(pair):
    a, b = pair
    async with live_pair(a, b) as (bundle, body, _la, lb):
        hello = await post(
            b.s,
            body.issuer_endpoint,
            a.s.certificate_pem,
            "/api/v1/federation/hello",
            {"protocol_version": 1, "sender_node_id": b.s.node_id},
        )
        assert hello["node_id"] == a.s.node_id
        with pytest.raises(PeerError, match="REMOTE_REJECTED"):
            await post(
                b.s,
                body.issuer_endpoint,
                a.s.certificate_pem,
                "/api/v1/federation/hello",
                {"protocol_version": 2, "sender_node_id": b.s.node_id},
            )
        with pytest.raises(PeerError, match="REMOTE_REJECTED"):
            await post(
                b.s,
                body.issuer_endpoint,
                a.s.certificate_pem,
                "/api/v1/federation/hello",
                {"protocol_version": 1, "sender_node_id": a.s.node_id},
            )
        receipt = await post(
            b.s,
            body.issuer_endpoint,
            a.s.certificate_pem,
            f"/api/v1/federation/invites/{bundle.invite_id}/redeem",
            {
                "protocol_version": 1,
                "sender_node_id": b.s.node_id,
                "token": bundle.token.get_secret_value(),
            },
        )
        await b.s.finish_accept(b.admin, body, receipt)
        await authorize(a, b)
        # Both peers can authenticate, but accepting grants no B-side channel exposure.
        hello_b = await post(
            a.s,
            endpoint(lb.port),
            b.s.certificate_pem,
            "/api/v1/federation/hello",
            {"protocol_version": 1, "sender_node_id": a.s.node_id},
        )
        assert hello_b["node_id"] == b.s.node_id
        assert await count(b.s, PeerGrant) == 1


async def test_tls_rejects_untrusted_certificate_before_http(pair, tmp_path):
    a, b = pair
    async with live_pair(a, b) as (_, body, _, __):
        cert, key = create_credentials(tmp_path / "forged", b.s.node_id)
        b.s.cert_path, b.s.key_path = cert, key
        with pytest.raises(PeerError, match="PEER_UNAVAILABLE"):
            await post(
                b.s,
                body.issuer_endpoint,
                a.s.certificate_pem,
                "/api/v1/federation/hello",
                {"protocol_version": 1, "sender_node_id": b.s.node_id},
            )


async def test_keepalive_old_pin_denied_even_when_socket_close_delayed(
    pair, tmp_path, monkeypatch
):
    import json

    from anygarden.federation.certificates import tls_context

    a, b = pair
    async with live_pair(a, b) as (_, _body, listener, _):
        context = tls_context(
            b.s.cert_path, b.s.key_path, [a.s.certificate_pem], server=False
        )
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", listener.port, ssl=context, server_hostname="node.test"
        )

        async def hello_on_same_connection():
            payload = json.dumps(
                {"protocol_version": 1, "sender_node_id": b.s.node_id}
            ).encode()
            writer.write(
                b"POST /api/v1/federation/hello HTTP/1.1\r\nHost: node.test\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\n\r\n"
                + payload
            )
            await writer.drain()
            headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            length = next(
                int(h.split(b":")[1])
                for h in headers.split(b"\r\n")
                if h.lower().startswith(b"content-length:")
            )
            await reader.readexactly(length)
            return int(headers.split(b" ")[1])

        try:
            assert await hello_on_same_connection() == 200
            cert, _key = create_credentials(tmp_path / "rotated", b.s.node_id)
            monkeypatch.setattr(a.s, "close_connections", lambda node: None)
            await a.s.rotate(a.admin, b.s.node_id, cert.read_text(), 1)
            assert await hello_on_same_connection() == 401
        finally:
            writer.close()
            await writer.wait_closed()


async def test_rotation_actually_requests_socket_shutdown(pair, tmp_path):
    from anygarden.federation.certificates import tls_context

    a, b = pair
    async with live_pair(a, b) as (_, _body, listener, _):
        context = tls_context(
            b.s.cert_path, b.s.key_path, [a.s.certificate_pem], server=False
        )
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", listener.port, ssl=context, server_hostname="node.test"
        )
        # Wait until the accepted connection has been registered on the server.
        for _ in range(100):
            if a.s._closers.get(b.s.node_id):
                break
            await asyncio.sleep(0.01)
        assert a.s._closers.get(b.s.node_id)
        cert, _key = create_credentials(tmp_path / "rotation-close", b.s.node_id)
        await a.s.rotate(a.admin, b.s.node_id, cert.read_text(), 1)
        assert await asyncio.wait_for(reader.read(1), 2) == b""
        writer.close()
        await writer.wait_closed()


async def test_outbox_failed_delivery_does_not_undo_local_revoke(pair, monkeypatch):
    a, b = pair
    await admit(a, b)
    await a.s.revoke(a.admin, b.s.node_id)

    async def unavailable(*args, **kwargs):
        raise PeerError("PEER_UNAVAILABLE", 503)

    monkeypatch.setattr("anygarden.federation.transport.post", unavailable)
    assert await a.s.deliver_controls() == 0
    with pytest.raises(PeerError):
        await authorize(a, b)
    async with a.s.sessions() as db:
        event = (await db.execute(select(PeerControlEvent))).scalar_one()
        assert not event.delivered


async def test_outbox_actual_tls_delivery_ack_and_idempotence(pair):
    a, b = pair
    async with live_pair(a, b) as (bundle, body, _la, lb):
        receipt = await a.s.redeem(
            b.s.identity,
            b.s.node_id,
            str(bundle.invite_id),
            bundle.token.get_secret_value(),
        )
        await b.s.finish_accept(b.admin, body, receipt)
        async with a.s.sessions.begin() as db:
            await db.execute(
                update(Peer)
                .where(Peer.node_id == b.s.node_id)
                .values(endpoint=endpoint(lb.port).model_dump())
            )
        await a.s.revoke(a.admin, b.s.node_id)
        assert await a.s.deliver_controls() == 1
        assert await a.s.deliver_controls() == 0
        async with b.s.sessions() as db:
            assert (await db.get(Peer, a.s.node_id)).state == "revoked"


async def test_grant_update_epoch_and_observer_scope(pair):
    from anygarden.federation.schemas import GrantReplace

    a, b = pair
    await admit(a, b)
    body = GrantReplace(
        scope=Scope(
            channel_id=a.channel,
            actors=[Principal(node_id=b.s.node_id, kind="agent", principal_id=b.actor)],
            capabilities=["channel.read"],
            role="observer",
        ),
        expected_grant_epoch=1,
    )
    result = await a.s.replace_grant(a.admin, b.s.node_id, a.channel, body)
    assert result == {"grant_epoch": 2, "policy_epoch": 2}
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await authorize(a, b)
    with pytest.raises(PeerError, match="SCOPE_DENIED"):
        await authorize(a, b, epoch=2)
    await authorize(a, b, action="channel.read", epoch=2)
    await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await authorize(a, b, action="channel.read", epoch=2)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await a.s.replace_grant(
            a.admin,
            b.s.node_id,
            a.channel,
            body.model_copy(update={"expected_grant_epoch": 3}),
        )


async def test_acceptance_response_after_local_revocation_cannot_activate_peer(pair):
    a, b = pair
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    body = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, body)
    receipt = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    await b.s.revoke(b.admin, a.s.node_id)
    with pytest.raises(PeerError, match="ACCEPTANCE_CONFLICT"):
        await b.s.finish_accept(b.admin, body, receipt)


async def test_local_admin_api_uses_fresh_db_role_and_never_echoes_secret(pair):
    from anygarden.auth.dependencies import Identity
    from anygarden.dependencies import get_admin_identity
    from anygarden.federation.router import mount_admin
    from fastapi import FastAPI

    a, b = pair
    app = FastAPI()
    mount_admin(app, a.s)
    # Exercise service-side fresh-role check even when JWT dependency says admin.
    app.dependency_overrides[get_admin_identity] = lambda: Identity(
        kind="user", id=a.admin
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/api/v1/node/invites", json=invitation(a, b).model_dump(mode="json")
        )
        assert r.status_code == 201
        assert r.headers["cache-control"] == "no-store"
        token = r.json()["token"]
        assert len(token) > 32
        listed = await client.get("/api/v1/node/invites")
        assert token not in listed.text
        invalid = await client.post("/api/v1/node/invites", json={"token": token})
        assert invalid.status_code == 400
        assert token not in invalid.text
        async with a.s.sessions.begin() as db:
            await db.execute(update(User).values(is_admin=False))
        assert (await client.get("/api/v1/node/peers")).status_code == 403
    async with a.s.sessions() as db:
        row = (await db.execute(select(PeerInvite))).scalar_one()
        assert row.token_hash != token and len(row.token_hash) == 64


def test_generated_keys_private_and_never_overwritten(tmp_path):
    import os

    cert, key = create_credentials(tmp_path / "credentials", uid())
    if os.name != "nt":
        assert key.stat().st_mode & 0o777 == 0o600
        assert cert.stat().st_mode & 0o777 == 0o600
    with pytest.raises(PeerError, match="CREDENTIALS_EXIST"):
        create_credentials(tmp_path / "credentials", uid())


async def test_sqlite_authorization_effect_and_revoke_are_serialized(pair):
    from anygarden.db.models import Project

    a, b = pair
    await admit(a, b)
    authorized, permit_commit, revoke_attempted = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    project_id = uid()
    sequence = []

    async def command():
        async with a.s.sessions.begin() as db:
            await a.s.authorize(
                db,
                b.s.identity,
                sender_node_id=b.s.node_id,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                principal=Principal(
                    node_id=b.s.node_id, kind="agent", principal_id=b.actor
                ),
                action="message.send",
                grant_epoch=1,
            )
            authorized.set()
            await permit_commit.wait()
            db.add(Project(id=project_id, name="authorized effect"))
        sequence.append("effect_committed")

    async def revoke():
        await authorized.wait()
        revoke_attempted.set()
        await a.s.revoke(a.admin, b.s.node_id)
        sequence.append("revoke_committed")

    first = asyncio.create_task(command())
    second = asyncio.create_task(revoke())
    try:
        await revoke_attempted.wait()
        await asyncio.sleep(0.05)
        assert not second.done()  # genuinely separate pooled connections
        permit_commit.set()
        await asyncio.wait_for(asyncio.gather(first, second), 5)
        assert sequence == ["effect_committed", "revoke_committed"]
        with pytest.raises(PeerError):
            await authorize(a, b)
        async with a.s.sessions() as db:
            assert await db.get(Project, project_id)
    finally:
        permit_commit.set()
        await asyncio.gather(first, second, return_exceptions=True)


async def test_mirror_delivery_needs_ack_and_current_grant_and_consent(pair):
    a, b = pair
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    body = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, body)

    async def deliver(epoch=1):
        async with b.s.sessions.begin() as db:
            return await b.s.authorize_delivery(
                db,
                a.s.identity,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                grant_epoch=epoch,
            )

    with pytest.raises(PeerError):
        await deliver()
    receipt = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    await b.s.finish_accept(b.admin, body, receipt)
    assert (await deliver())["grant_epoch"] == 1
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await deliver(2)
    event = uid()
    await b.s.receive_grant_revoke(a.s.identity, a.s.node_id, event, 1, a.channel, 2)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await deliver()
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await b.s.begin_accept(b.admin, body)
    async with b.s.sessions() as db:
        assert (await db.get(Peer, a.s.node_id)).state == "active"
        # Mirror grants are never owned by this node.
        assert (
            await db.scalar(
                select(func.count())
                .select_from(PeerGrant)
                .where(PeerGrant.authority_node_id == b.s.node_id)
            )
            == 0
        )


async def test_targeted_grant_revocation_delivered_without_peer_revocation(pair):
    a, b = pair
    async with live_pair(a, b) as (bundle, body, _la, lb):
        receipt = await a.s.redeem(
            b.s.identity,
            b.s.node_id,
            str(bundle.invite_id),
            bundle.token.get_secret_value(),
        )
        await b.s.finish_accept(b.admin, body, receipt)
        async with a.s.sessions.begin() as db:
            await db.execute(
                update(Peer)
                .where(Peer.node_id == b.s.node_id)
                .values(endpoint=endpoint(lb.port).model_dump())
            )
        await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
        assert await a.s.deliver_controls() == 1
        async with b.s.sessions.begin() as db:
            assert (await db.get(Peer, a.s.node_id)).state == "active"
            with pytest.raises(PeerError, match="GRANT_DENIED"):
                await b.s.authorize_delivery(
                    db,
                    a.s.identity,
                    authority_node_id=a.s.node_id,
                    channel_id=a.channel,
                    grant_epoch=1,
                )


async def test_malformed_remote_receipt_cannot_activate_peer(pair):
    a, b = pair
    bundle = await a.s.create_invite(a.admin, invitation(a, b))
    body = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, body)
    receipt = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    for grants in (
        [],
        [{"channel_id": a.channel, "grant_epoch": True}],
        [{"channel_id": uid(), "grant_epoch": 1}],
    ):
        with pytest.raises(PeerError, match="RECEIPT_DENIED"):
            await b.s.finish_accept(b.admin, body, {**receipt, "grants": grants})
    async with b.s.sessions() as db:
        assert (await db.get(Peer, a.s.node_id)).state == "pending"


def test_peer_migration_schema_matches_product_models(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    path = tmp_path / "migration.db"
    cfg = Config()
    cfg.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent.parent / "anygarden/db/migrations"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    assert ScriptDirectory.from_config(cfg).get_heads() == ["069_reactions_wake_triggers"]
    command.upgrade(cfg, "063")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        for table in Base.metadata.tables.values():
            if table.name.startswith("federation_"):
                columns = {c["name"] for c in inspector.get_columns(table.name)}
                assert columns == set(table.columns.keys())
    finally:
        engine.dispose()
    command.downgrade(cfg, "063")
    engine = create_engine(f"sqlite:///{path}")
    try:
        assert not any(
            t.startswith("federation_") for t in inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


async def test_actual_jwt_admin_gate_and_peer_disabled_mode(pair):
    from anygarden.auth.jwt import create_user_token
    from anygarden.federation.router import mount_admin
    from fastapi import FastAPI

    a, _b = pair
    secret = "synthetic-local-test-secret-not-a-real-credential"
    app = FastAPI()
    app.state.session_factory = a.s.sessions
    app.state.config = SimpleNamespace(jwt_secret=secret)
    mount_admin(app, a.s)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/node/peers")).status_code == 401
        token = create_user_token(a.admin, "a@example.test", False, secret=secret)
        assert (
            await client.get(
                "/api/v1/node/peers", headers={"Authorization": f"Bearer {token}"}
            )
        ).status_code == 403
        token = create_user_token(a.admin, "a@example.test", True, secret=secret)
        assert (
            await client.get(
                "/api/v1/node/peers", headers={"Authorization": f"Bearer {token}"}
            )
        ).status_code == 200
        app.state.peer_service = None
        assert (
            await client.get(
                "/api/v1/node/peers", headers={"Authorization": f"Bearer {token}"}
            )
        ).status_code == 503


async def test_expiry_revoke_invite_and_preapproved_scope_are_enforced(pair):
    a, b = pair
    invitation_body = invitation(a, b)
    forged = invitation_body.model_copy(update={"intended_node_id": uid()})
    with pytest.raises(PeerError, match="IDENTITY_MISMATCH"):
        await a.s.create_invite(a.admin, forged)
    bundle = await a.s.create_invite(a.admin, invitation_body)
    await a.s.revoke_invite(a.admin, str(bundle.invite_id))
    with pytest.raises(PeerError):
        await a.s.redeem(
            b.s.identity,
            b.s.node_id,
            str(bundle.invite_id),
            bundle.token.get_secret_value(),
        )
    await admit(a, b)
    async with a.s.sessions.begin() as db:
        await db.execute(
            update(PeerGrant).values(expires_at=now() - timedelta(seconds=1))
        )
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await authorize(a, b)


async def test_tls_rejects_absent_client_certificate(pair):
    import ssl

    a, b = pair
    async with live_pair(a, b) as (_bundle, _body, listener, _other):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.load_verify_locations(cadata=a.s.certificate_pem)
        writer = None
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", listener.port, ssl=ctx, server_hostname="node.test"
            )
            writer.write(
                b"GET /api/v1/federation/hello HTTP/1.1\r\nHost: node.test\r\n\r\n"
            )
            await writer.drain()
            assert await asyncio.wait_for(reader.read(100), 2) == b""
        except (ssl.SSLError, ConnectionError):
            pass  # TLS 1.2/1.3 report rejection at different client operations.
        finally:
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ssl.SSLError, ConnectionError):
                    pass


async def test_client_refuses_redirect_without_forwarding_credentials(pair):
    from anygarden.federation.certificates import tls_context

    a, b = pair
    await a.s.create_invite(a.admin, invitation(a, b))
    requests = []

    async def redirect(reader, writer):
        try:
            data = await reader.readuntil(b"\r\n\r\n")
            requests.append(data.split(b"\r\n")[0])
            writer.write(
                b"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://169.254.169.254/secret\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        finally:
            writer.close()

    ctx = tls_context(a.s.cert_path, a.s.key_path, [b.s.certificate_pem], server=True)
    server = await asyncio.start_server(redirect, "127.0.0.1", 0, ssl=ctx)
    try:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(PeerError, match="REDIRECT_DENIED"):
            await post(
                b.s,
                endpoint(port),
                a.s.certificate_pem,
                "/api/v1/federation/hello",
                {"protocol_version": 1, "sender_node_id": b.s.node_id},
            )
        assert len(requests) == 1
    finally:
        server.close()
        await server.wait_closed()


# Principal definition copied verbatim from the contract SHA recorded in the
# fixture. Its examples cover both schema enum values (the original scenarios
# exercised only agent). Rebind identifiers to each isolated test node below.
PRINCIPAL_CONTRACT = json.loads(
    (Path(__file__).parent / "fixtures/federation_principals.json").read_text()
)


def test_principal_vocabulary_matches_original_contract():
    assert set(Principal.model_json_schema()["properties"]["kind"]["enum"]) == set(
        PRINCIPAL_CONTRACT["schema"]["properties"]["kind"]["enum"]
    )
    for fixture in PRINCIPAL_CONTRACT["principals"]:
        assert Principal.model_validate(fixture).model_dump(mode="json") == fixture
    with pytest.raises(ValidationError):
        Principal.model_validate(
            {**PRINCIPAL_CONTRACT["principals"][0], "kind": "user"}
        )


@pytest.mark.parametrize(
    "fixture", PRINCIPAL_CONTRACT["principals"], ids=lambda p: p["kind"]
)
async def test_contract_principal_invite_accept_and_authorize(pair, fixture):
    a, b = pair
    principal = Principal.model_validate({**fixture, "node_id": b.s.node_id})
    body = invitation(a, b)
    body.scopes[0].actors = [principal]
    bundle = await a.s.create_invite(a.admin, body)
    args = (
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    receipt = await a.s.redeem(*args)
    accept = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, accept)
    await b.s.finish_accept(b.admin, accept, receipt)
    assert await a.s.redeem(*args) == receipt
    assert await b.s.begin_accept(b.admin, accept) == receipt
    async with a.s.sessions.begin() as db:
        auth = await a.s.authorize(
            db,
            b.s.identity,
            sender_node_id=b.s.node_id,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            principal=principal,
            action="message.send",
            grant_epoch=1,
        )
        assert auth.principal == principal
    async with b.s.sessions() as db:
        grant = await db.get(PeerGrant, (a.s.node_id, a.s.node_id, a.channel))
        assert grant.actors == [principal.model_dump(mode="json")]


async def withdraw_approval(node, key, change):
    async with node.s.sessions.begin() as db:
        # Use the same peer lock as a product policy writer.
        await node.s.lock_peer(db, key[0])
        if change == "admin":
            (await db.get(User, node.admin)).is_admin = False
        elif change == "archive":
            (await db.get(Room, key[2])).archived_at = now()
        elif change == "dm":
            (await db.get(Room, key[2])).is_dm = True
        elif change == "consent":
            (await db.get(PeerConsent, key)).active = False
        elif change == "inactive":
            (await db.get(PeerGrant, key)).active = False
        elif change == "expired":
            (await db.get(PeerGrant, key)).expires_at = now() - timedelta(seconds=1)
        elif change == "epoch":
            (await db.get(PeerGrant, key)).epoch += 1
        else:
            raise AssertionError(change)


@pytest.mark.parametrize(
    "change,code",
    [
        ("admin", "ADMIN_REQUIRED"),
        ("archive", "CHANNEL_DENIED"),
        ("dm", "CHANNEL_DENIED"),
        ("consent", "LOCAL_POLICY_DENIED"),
        ("inactive", "GRANT_DENIED"),
        ("expired", "GRANT_DENIED"),
        ("epoch", "GRANT_DENIED"),
    ],
)
async def test_redeem_receipt_rechecks_current_channel_approval(pair, change, code):
    a, b = pair
    bundle, receipt = await admit(a, b)
    audit_count = await count(a.s, PeerAudit)
    await withdraw_approval(a, (b.s.node_id, a.s.node_id, a.channel), change)
    with pytest.raises(PeerError, match=code):
        await authorize(a, b)
    with pytest.raises(PeerError, match=code):
        await a.s.redeem(
            b.s.identity,
            b.s.node_id,
            str(bundle.invite_id),
            bundle.token.get_secret_value(),
        )
    assert await count(a.s, PeerAudit) == audit_count
    assert await count(a.s, PeerGrant) == 1
    async with a.s.sessions() as db:
        assert (await db.get(PeerInvite, str(bundle.invite_id))).receipt == receipt


@pytest.mark.parametrize("path", ["begin", "finish"])
@pytest.mark.parametrize(
    "change,code",
    [
        ("admin", "ADMIN_REQUIRED"),
        ("consent", "LOCAL_POLICY_DENIED"),
        ("inactive", "GRANT_DENIED"),
        ("expired", "GRANT_DENIED"),
        ("epoch", "GRANT_DENIED"),
    ],
)
async def test_accept_receipt_rechecks_current_local_approval(pair, path, change, code):
    a, b = pair
    bundle, receipt = await admit(a, b)
    accept = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    # A different still-active administrator cannot recover a receipt whose
    # original grant approver has lost authority.
    retry_admin = uid()
    async with b.s.sessions.begin() as db:
        db.add(
            User(
                id=retry_admin,
                email="retry@example.test",
                password_hash="synthetic",
                is_admin=True,
            )
        )
    audit_count = await count(b.s, PeerAudit)
    await withdraw_approval(b, (a.s.node_id, a.s.node_id, a.channel), change)
    with pytest.raises(PeerError, match=code):
        async with b.s.sessions.begin() as db:
            await b.s.authorize_delivery(
                db,
                a.s.identity,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                grant_epoch=1,
            )
    with pytest.raises(PeerError, match=code):
        if path == "begin":
            await b.s.begin_accept(retry_admin, accept)
        else:
            await b.s.finish_accept(retry_admin, accept, receipt)
    assert await count(b.s, PeerAudit) == audit_count
    assert await count(b.s, PeerGrant) == 1
    async with b.s.sessions() as db:
        assert (await db.get(PeerAcceptance, str(bundle.invite_id))).receipt == receipt
