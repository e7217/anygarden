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
