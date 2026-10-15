"""#842 — longer-lived peer certificates; an expired one stops federation only."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner
from httpx import ASGITransport, AsyncClient

from anygarden.app import (
    _compose_federation_services,
    _federation_listener_enabled,
    create_app,
)
from anygarden.auth.jwt import create_user_token
from anygarden.cli import dispatch
from anygarden.config import AnygardenSettings
from anygarden.db.models import Base
from anygarden.federation.certificates import (
    CERTIFICATE_LIFETIME,
    certificate_expires_at,
    create_credentials,
    inspect_certificate,
    is_expired,
)
from anygarden.federation.credentials_setup import (
    CredentialSetupError,
    CredentialsExpired,
    ensure_peer_credentials,
)
from anygarden.federation.errors import PeerError

from .test_app_federation_wiring import _seed_admin, wiring  # noqa: F401


def expired_pair(directory: Path, node_id: str | None = None) -> tuple[Path, Path]:
    """A structurally valid pair whose validity ended yesterday."""
    return create_credentials(
        directory,
        node_id or str(uuid4()),
        now=datetime.now(UTC) - CERTIFICATE_LIFETIME - timedelta(days=1),
    )


def _config(**values) -> AnygardenSettings:
    config = AnygardenSettings(_env_file=None)
    for key, value in values.items():
        setattr(config, key, value)
    return config


def test_new_certificates_last_two_years(tmp_path):
    cert, _ = create_credentials(tmp_path / "peer", str(uuid4()))
    lifetime = certificate_expires_at(cert.read_text()) - datetime.now(UTC)
    assert timedelta(days=729) < lifetime <= timedelta(days=730)


def test_expired_certificate_keeps_its_identity_when_validity_is_not_checked(
    tmp_path,
):
    node_id = str(uuid4())
    cert, _ = expired_pair(tmp_path / "peer", node_id)
    pem = cert.read_text()

    with pytest.raises(PeerError, match="CERTIFICATE_DENIED"):
        inspect_certificate(pem)
    assert inspect_certificate(pem, check_validity=False).node_id == node_id
    assert is_expired(pem) is True


def test_is_expired_is_false_for_a_current_certificate(tmp_path):
    cert, _ = create_credentials(tmp_path / "peer", str(uuid4()))
    assert is_expired(cert.read_text()) is False


def test_structurally_broken_certificates_stay_denied_without_validity_check():
    with pytest.raises(PeerError, match="CERTIFICATE_DENIED"):
        inspect_certificate("not a certificate", check_validity=False)


@pytest.mark.req("FED-13")
def test_expired_pair_raises_a_distinct_error_with_its_identity(tmp_path):
    node_id = str(uuid4())
    cert, key = expired_pair(tmp_path / "peer", node_id)
    before = (cert.read_bytes(), key.read_bytes())

    with pytest.raises(CredentialsExpired) as exc:
        ensure_peer_credentials(tmp_path, _config())

    assert isinstance(exc.value, CredentialSetupError)
    assert exc.value.identity.node_id == node_id
    assert exc.value.expired_at < datetime.now(UTC)
    assert "init-credentials --force" in str(exc.value)
    assert (cert.read_bytes(), key.read_bytes()) == before


def test_corrupt_pair_is_still_unusable_not_expired(tmp_path):
    peer = tmp_path / "peer"
    peer.mkdir()
    (peer / "peer-cert.pem").write_text("not a certificate")
    (peer / "peer-key.pem").write_text("not a key")

    with pytest.raises(CredentialSetupError, match="unusable") as exc:
        ensure_peer_credentials(tmp_path, _config())
    assert not isinstance(exc.value, CredentialsExpired)


class _StopBeforeServing(Exception):
    pass


def _start(tmp_path: Path, monkeypatch):
    import anygarden.app

    seen = {}

    def fake_create_app(config):
        seen["config"] = config
        raise _StopBeforeServing

    monkeypatch.setattr(anygarden.app, "create_app", fake_create_app)
    result = CliRunner().invoke(
        dispatch, ["start", "--data-dir", str(tmp_path), "--peer-port", "8451"]
    )
    return result, seen.get("config")


@pytest.mark.req("FED-13")
def test_start_with_expired_pair_boots_without_the_peer_listener(tmp_path, monkeypatch):
    expired_pair(tmp_path / "peer")
    result, config = _start(tmp_path, monkeypatch)

    assert isinstance(result.exception, _StopBeforeServing), result.output
    assert config.peer_listen_port is None
    assert "expired" in result.output
    assert "init-credentials --force" in result.output


@pytest.mark.req("FED-14")
def test_start_warns_when_the_certificate_expires_within_30_days(tmp_path, monkeypatch):
    create_credentials(
        tmp_path / "peer",
        str(uuid4()),
        now=datetime.now(UTC) - CERTIFICATE_LIFETIME + timedelta(days=10),
    )
    result, config = _start(tmp_path, monkeypatch)

    assert isinstance(result.exception, _StopBeforeServing), result.output
    assert config.peer_listen_port == 8451
    assert "expires in 9 days" in result.output or "expires in 10 days" in result.output


def test_start_with_a_fresh_pair_prints_no_expiry_warning(tmp_path, monkeypatch):
    create_credentials(tmp_path / "peer", str(uuid4()))
    result, _ = _start(tmp_path, monkeypatch)
    assert "expires in" not in result.output
    assert "expired" not in result.output


def test_force_renewal_of_an_expired_pair_keeps_the_node_id(tmp_path):
    node_id = str(uuid4())
    expired_pair(tmp_path / "peer", node_id)
    assert not (tmp_path / "node-identity.json").exists()

    result = CliRunner().invoke(
        dispatch,
        ["federation", "init-credentials", "--data-dir", str(tmp_path), "--force"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    cert = (tmp_path / "peer" / "peer-cert.pem").read_text()
    assert inspect_certificate(cert).node_id == node_id
    assert "valid for 2 years" in result.output


def test_init_credentials_reuses_node_identity_json_shape(tmp_path):
    # Unchanged contract: node-identity.json still seeds a first pair.
    node_id = str(uuid4())
    (tmp_path / "node-identity.json").write_text(json.dumps({"node_id": node_id}))
    identity, created = ensure_peer_credentials(tmp_path, _config())
    assert created and identity.node_id == node_id


# --------------------------------------------------------------------------
# App layer: federation off with a stated reason, the node still boots.


async def _composed_app(wiring):
    engine, factory, config, _ = wiring
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    _compose_federation_services(app)
    return app


@pytest.mark.req("FED-13")
async def test_expired_credentials_disable_federation_with_their_own_code(wiring):
    _, factory, config, peer_dir = wiring
    expired_pair(peer_dir)
    app = await _composed_app(wiring)
    admin = await _seed_admin(factory)
    token = create_user_token(
        admin, "admin@example.test", True, secret=config.jwt_secret
    )

    assert app.state.peer_service is None
    assert app.state.federation_disabled_reason == "CERTIFICATE_EXPIRED"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get(
            "/api/v1/node/peers", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 503
    assert r.json() == {"code": "CERTIFICATE_EXPIRED"}


async def test_absent_credentials_keep_the_generic_disabled_code(wiring):
    app = await _composed_app(wiring)
    assert getattr(app.state, "federation_disabled_reason", None) is None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get("/api/v1/node/peers")
    assert r.json() == {"code": "PEERING_DISABLED"}


@pytest.mark.req("FED-13")
async def test_requested_listener_is_skipped_not_fatal_when_expired(wiring):
    _, _, config, peer_dir = wiring
    expired_pair(peer_dir)
    config.peer_listen_port = 8451
    app = await _composed_app(wiring)
    assert _federation_listener_enabled(app) is False


async def test_requested_listener_still_fails_closed_for_unusable_credentials(wiring):
    _, _, config, peer_dir = wiring
    peer_dir.mkdir(parents=True)
    (peer_dir / "peer-cert.pem").write_text("not a certificate")
    (peer_dir / "peer-key.pem").write_text("not a key")
    config.peer_listen_port = 8451
    app = await _composed_app(wiring)
    with pytest.raises(RuntimeError, match="did not compose"):
        _federation_listener_enabled(app)


async def test_requested_listener_runs_with_current_credentials(wiring):
    _, _, config, peer_dir = wiring
    create_credentials(peer_dir, str(uuid4()))
    config.peer_listen_port = 8451
    app = await _composed_app(wiring)
    assert _federation_listener_enabled(app) is True


@pytest.mark.req("FED-14")
async def test_peers_listing_reports_each_peer_certificate_expiry(wiring):
    from anygarden.federation.models import Peer
    from anygarden.federation.service import now

    _, factory, config, peer_dir = wiring
    create_credentials(peer_dir, str(uuid4()))
    app = await _composed_app(wiring)
    admin = await _seed_admin(factory)
    other, _ = create_credentials(peer_dir.parent / "other", str(uuid4()))
    pem = other.read_text()
    identity = inspect_certificate(pem)
    async with factory.begin() as db:
        db.add(
            Peer(
                node_id=identity.node_id,
                certificate_pem=pem,
                fingerprint=identity.fingerprint,
                endpoint={
                    "url": "https://203.0.113.5:8451",
                    "approved_ips": ["203.0.113.5"],
                },
                state="active",
                epoch=1,
                approved_by=admin,
                updated_at=now(),
            )
        )
    token = create_user_token(
        admin, "admin@example.test", True, secret=config.jwt_secret
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.get(
            "/api/v1/node/peers", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 200, r.text
    (row,) = r.json()
    assert datetime.fromisoformat(
        row["certificate_expires_at"]
    ) == certificate_expires_at(pem)
