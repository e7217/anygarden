"""#593 product-app federation wiring (task #34).

create_app always mounts node/shared-channel routes; startup composes
PeerService/ChannelService only from persisted credentials created by the
explicit ``create_credentials`` setup step. Absent credentials keep every
route mounted but disabled (503), never 404, and never crash boot.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from anygarden.app import _compose_federation_services, create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, User
from anygarden.federation.certificates import create_credentials


@pytest.fixture()
async def wiring(tmp_path):
    """Engine + session factory + config pointing at a temp credential dir."""
    db_path = tmp_path / "wiring.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = build_session_factory(engine)
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{db_path}",
        jwt_secret="synthetic-local-test-secret-not-a-real-credential",
        peer_credentials_dir=tmp_path / "peer",
    )
    yield engine, factory, config, tmp_path / "peer"
    try:
        await engine.dispose()
    except Exception:  # pragma: no cover — best-effort teardown
        pass


async def _seed_admin(factory) -> str:
    admin_id = str(uuid4())
    async with factory.begin() as db:
        db.add(
            User(
                id=admin_id,
                email="admin@example.test",
                password_hash="x",
                is_admin=True,
            )
        )
    return admin_id


async def test_node_routes_are_mounted_but_disabled_without_credentials(
    wiring,
):
    engine, factory, config, _peer_dir = wiring
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    _compose_federation_services(app)
    admin = await _seed_admin(factory)
    token = create_user_token(
        admin, "admin@example.test", True, secret=config.jwt_secret
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Routes exist (no 404) and fail closed with an explicit code. The
        # service dependency resolves before auth, so disabled state answers
        # regardless of credentials.
        response = await client.get("/api/v1/node/peers")
        assert response.status_code == 503
        assert response.json()["code"] == "PEERING_DISABLED"
        response = await client.get(
            "/api/v1/node/peers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "PEERING_DISABLED"
        response = await client.post(
            "/api/v1/shared-channels/bindings",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )
        # Validation runs before the service dependency here, but any
        # service-bound path must be SHARING_DISABLED, not absent.
        assert response.status_code in (400, 422, 503)
        if response.status_code == 503:
            assert response.json()["code"] == "SHARING_DISABLED"
    assert app.state.peer_service is None
    assert app.state.channel_service is None


async def test_startup_composes_services_from_persisted_credentials(wiring):
    engine, factory, config, peer_dir = wiring
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    node_id = str(uuid4())
    cert_path, key_path = create_credentials(peer_dir, node_id)
    assert cert_path.name == "peer-cert.pem" and key_path.name == "peer-key.pem"
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    _compose_federation_services(app)
    from anygarden.federation.service import PeerService
    from anygarden.shared_channels.service import ChannelService

    assert isinstance(app.state.peer_service, PeerService)
    assert isinstance(app.state.channel_service, ChannelService)
    assert app.state.peer_service.node_id == node_id
    assert app.state.channel_service.node_id == node_id
    assert app.state.channel_service.peers is app.state.peer_service
    admin = await _seed_admin(factory)
    token = create_user_token(
        admin, "admin@example.test", True, secret=config.jwt_secret
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/node/peers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        # A service-bound shared-channel path now fails on policy, not on
        # the disabled service.
        response = await client.post(
            "/api/v1/shared-channels/bindings",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "authority_node_id": node_id,
                "channel_id": str(uuid4()),
                "local_room_id": str(uuid4()),
            },
        )
        assert response.status_code != 503


async def test_explicit_channel_service_injection_is_not_replaced(wiring):
    engine, factory, config, peer_dir = wiring
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    create_credentials(peer_dir, str(uuid4()))
    sentinel = object()
    app = create_app(config, channel_service=sentinel)
    app.state.engine = engine
    app.state.session_factory = factory
    _compose_federation_services(app)
    assert app.state.channel_service is sentinel
    # The peer service is still composed; only the channel service is
    # caller-owned here.
    assert app.state.peer_service is not None


async def test_partial_credentials_stay_disabled(wiring, caplog):
    engine, factory, config, peer_dir = wiring
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    cert_path, _key_path = create_credentials(peer_dir, str(uuid4()))
    _key_path.unlink()
    assert cert_path.exists()
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    _compose_federation_services(app)
    assert app.state.peer_service is None
    assert app.state.channel_service is None


# --------------------------------------------------------------------------
# #593 Phase B — shared-room metadata visibility + bindings listing


@pytest.fixture()
async def composed(wiring):
    """Real composition plus authority/mirror shared rooms and members."""
    from datetime import timedelta

    from anygarden.federation.models import Peer, PeerGrant
    from anygarden.federation.service import now
    from anygarden.db.models import Participant, Room
    from anygarden.shared_channels.models import SharedParticipant

    engine, factory, config, peer_dir = wiring
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    node_id = str(uuid4())
    create_credentials(peer_dir, node_id)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    _compose_federation_services(app)
    assert app.state.channel_service is not None

    admin, member, outsider = str(uuid4()), str(uuid4()), str(uuid4())
    plain, authority_room, mirror_room = str(uuid4()), str(uuid4()), str(uuid4())
    remote_authority, mirror_channel = str(uuid4()), str(uuid4())
    principal = {
        "node_id": node_id,
        "kind": "human",
        "principal_id": member,
    }
    async with factory.begin() as db:
        for uid_, email in (
            (admin, "admin@example.test"),
            (member, "member@example.test"),
            (outsider, "outsider@example.test"),
        ):
            db.add(
                User(
                    id=uid_,
                    email=email,
                    password_hash="x",
                    is_admin=uid_ == admin,
                )
            )
        db.add(Room(id=plain, name="plain"))
        db.add(Room(id=authority_room, name="authority-side shared"))
        db.add(Room(id=mirror_room, name="mirror-side shared"))
        for room in (plain, authority_room, mirror_room):
            db.add(
                Participant(
                    id=str(uuid4()), room_id=room, user_id=member, role="member"
                )
            )
        await db.flush()
        from anygarden.shared_channels.models import ChannelStream

        db.add(
            ChannelStream(
                authority_node_id=node_id,
                channel_id=authority_room,
                local_room_id=authority_room,
            )
        )
        db.add(
            ChannelStream(
                authority_node_id=remote_authority,
                channel_id=mirror_channel,
                local_room_id=mirror_room,
            )
        )
        db.add(
            SharedParticipant(
                authority_node_id=node_id,
                channel_id=authority_room,
                node_id=node_id,
                kind="human",
                principal_id=member,
                role="member",
                active=True,
                revision=1,
            )
        )
        db.add(
            Peer(
                node_id=remote_authority,
                certificate_pem="—test—",
                fingerprint="0" * 64,
                endpoint={},
                state="active",
                epoch=1,
                approved_by=admin,
                updated_at=now(),
            )
        )
        db.add(
            PeerGrant(
                peer_node_id=remote_authority,
                authority_node_id=remote_authority,
                channel_id=mirror_channel,
                epoch=1,
                active=True,
                actors=[principal],
                capabilities=["message.send"],
                role="member",
                expires_at=now() + timedelta(hours=1),
                approved_by=admin,
            )
        )
    return {
        "app": app,
        "ids": {
            "node": node_id,
            "admin": admin,
            "member": member,
            "outsider": outsider,
            "plain": plain,
            "authority": authority_room,
            "mirror": mirror_room,
            "remote": remote_authority,
            "channel": mirror_channel,
            "principal": principal,
        },
        "factory": factory,
        "config": config,
    }


def _token(config, uid_, email, admin=False):
    return create_user_token(uid_, email, admin, secret=config.jwt_secret)


async def test_shared_rooms_listed_for_visible_participants(composed):
    app, ids, config = composed["app"], composed["ids"], composed["config"]
    token = _token(config, ids["member"], "member@example.test")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/rooms", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        listed = {room["id"] for room in response.json()}
        assert ids["plain"] in listed
        assert ids["authority"] in listed
        assert ids["mirror"] in listed


async def test_global_admin_asymmetry_and_outsider_exclusion(composed):
    app, ids, config = composed["app"], composed["ids"], composed["config"]
    admin_token = _token(config, ids["admin"], "admin@example.test", admin=True)
    outsider_token = _token(config, ids["outsider"], "outsider@example.test")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/rooms",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        listed = {room["id"] for room in response.json()}
        assert ids["plain"] in listed
        # B-2: global admin bypass never re-includes shared rooms.
        assert ids["authority"] not in listed
        assert ids["mirror"] not in listed
        response = await client.get(
            "/api/v1/rooms",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert response.status_code == 200
        assert response.json() == []


async def test_tombstone_and_grant_revocation_hide_on_next_request(composed):
    from anygarden.federation.models import PeerGrant
    from anygarden.shared_channels.models import SharedParticipant

    app, ids, config, factory = (
        composed["app"],
        composed["ids"],
        composed["config"],
        composed["factory"],
    )
    token = _token(config, ids["member"], "member@example.test")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def listed_ids() -> set[str]:
            response = await client.get(
                "/api/v1/rooms", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 200
            return {room["id"] for room in response.json()}

        assert await listed_ids() == {
            ids["plain"],
            ids["authority"],
            ids["mirror"],
        }
        async with factory.begin() as db:
            roster = await db.get(
                SharedParticipant,
                (
                    ids["node"],
                    ids["authority"],
                    ids["node"],
                    "human",
                    ids["member"],
                ),
            )
            roster.active = False
            grant = await db.get(
                PeerGrant,
                (ids["remote"], ids["remote"], ids["channel"]),
            )
            grant.active = False
        # B-7: per-request re-check — no cache, next listing hides both.
        assert await listed_ids() == {ids["plain"]}


async def test_room_detail_metadata_only_and_subresources_409(composed):
    app, ids, config = composed["app"], composed["ids"], composed["config"]
    token = _token(config, ids["member"], "member@example.test")
    outsider_token = _token(config, ids["outsider"], "outsider@example.test")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(
            f"/api/v1/rooms/{ids['authority']}", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["id"] == ids["authority"]
        # Condition 1(b): rooms-API sub-resources stay 409 for shared rooms.
        response = await client.get(
            f"/api/v1/rooms/{ids['authority']}/messages", headers=headers
        )
        assert response.status_code == 409
        # Non-participants get the existence-hiding 404.
        response = await client.get(
            f"/api/v1/rooms/{ids['authority']}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert response.status_code == 404


async def test_bindings_listing_is_admin_only_metadata(composed):
    app, ids, config = composed["app"], composed["ids"], composed["config"]
    admin_token = _token(config, ids["admin"], "admin@example.test", admin=True)
    member_token = _token(config, ids["member"], "member@example.test")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/shared-channels/bindings")
        assert response.status_code == 401
        response = await client.get(
            "/api/v1/shared-channels/bindings",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert response.status_code == 403
        response = await client.get(
            "/api/v1/shared-channels/bindings",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        bindings = response.json()
        assert {b["authority_node_id"] for b in bindings} == {
            ids["node"],
            ids["remote"],
        }
        for binding in bindings:
            assert set(binding) == {
                "authority_node_id",
                "channel_id",
                "local_room_id",
                "last_seq",
                "applied_seq",
            }


async def test_saved_and_search_scopes_stay_excluded(composed):
    from types import SimpleNamespace

    from anygarden.rooms.authorization import accessible_room_ids

    app, ids, factory = composed["app"], composed["ids"], composed["factory"]
    identity = SimpleNamespace(kind="user", id=ids["member"], claims=None)
    async with factory() as db:
        for scope in ("saved.messages", "search.messages"):
            allowed = await accessible_room_ids(
                db,
                identity=identity,
                scope=scope,
                channel_service=app.state.channel_service,
            )
            assert ids["authority"] not in allowed
            assert ids["mirror"] not in allowed


async def test_guest_identities_never_see_shared_rooms(composed):
    from types import SimpleNamespace

    from anygarden.shared_channels.visibility import visible_shared_room_ids

    app, ids, factory = composed["app"], composed["ids"], composed["factory"]
    guest = SimpleNamespace(kind="guest")
    async with factory() as db:
        assert (
            await visible_shared_room_ids(
                db,
                identity=guest,
                room_ids=frozenset({ids["authority"], ids["mirror"]}),
                channel_service=app.state.channel_service,
            )
            == frozenset()
        )
