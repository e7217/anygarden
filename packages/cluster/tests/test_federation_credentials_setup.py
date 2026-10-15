"""#839 — `start --peer-port` creates first-run credentials, never replaces them."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from uuid import uuid4

import pytest
from click.testing import CliRunner

from anygarden.cli import dispatch
from anygarden.config import AnygardenSettings
from anygarden.federation.certificates import create_credentials, inspect_certificate
from anygarden.federation.credentials_setup import (
    CredentialSetupError,
    ensure_peer_credentials,
)


def _config(**values) -> AnygardenSettings:
    config = AnygardenSettings(_env_file=None)
    for key, value in values.items():
        setattr(config, key, value)
    return config


@pytest.mark.req("FED-10")
def test_creates_pair_in_default_directory_when_absent(tmp_path):
    identity, created = ensure_peer_credentials(tmp_path, _config())

    assert created is True
    cert = tmp_path / "peer" / "peer-cert.pem"
    assert inspect_certificate(cert.read_text()) == identity
    assert stat.S_IMODE((tmp_path / "peer" / "peer-key.pem").stat().st_mode) == 0o600


@pytest.mark.req("FED-10")
def test_existing_pair_is_loaded_not_replaced(tmp_path):
    cert, key = create_credentials(tmp_path / "peer", str(uuid4()))
    before = (cert.read_bytes(), key.read_bytes())

    identity, created = ensure_peer_credentials(tmp_path, _config())

    assert created is False
    assert identity == inspect_certificate(cert.read_text())
    assert (cert.read_bytes(), key.read_bytes()) == before


@pytest.mark.req("FED-10")
def test_first_run_reuses_local_node_identity(tmp_path):
    node_id = str(uuid4())
    (tmp_path / "node-identity.json").write_text(json.dumps({"node_id": node_id}))

    identity, _ = ensure_peer_credentials(tmp_path, _config())

    assert identity.node_id == node_id


@pytest.mark.req("FED-06")
def test_partial_pair_is_refused_and_left_alone(tmp_path):
    peer = tmp_path / "peer"
    peer.mkdir()
    (peer / "peer-cert.pem").write_text("stale")

    with pytest.raises(CredentialSetupError, match="incomplete"):
        ensure_peer_credentials(tmp_path, _config())
    assert (peer / "peer-cert.pem").read_text() == "stale"
    assert not (peer / "peer-key.pem").exists()


@pytest.mark.req("FED-06")
def test_unusable_pair_is_refused_and_left_alone(tmp_path):
    peer = tmp_path / "peer"
    peer.mkdir()
    (peer / "peer-cert.pem").write_text("not a certificate")
    (peer / "peer-key.pem").write_text("not a key")

    with pytest.raises(CredentialSetupError, match="unusable"):
        ensure_peer_credentials(tmp_path, _config())
    assert (peer / "peer-cert.pem").read_text() == "not a certificate"


@pytest.mark.req("FED-06")
def test_missing_explicit_directory_is_refused(tmp_path):
    explicit = tmp_path / "mounted-credentials"

    with pytest.raises(CredentialSetupError, match="ANYGARDEN_PEER_CREDENTIALS_DIR"):
        ensure_peer_credentials(tmp_path, _config(peer_credentials_dir=explicit))
    assert not explicit.exists()


def test_symlinked_peer_directory_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "peer").symlink_to(target)

    with pytest.raises(CredentialSetupError, match="symlink"):
        ensure_peer_credentials(tmp_path, _config())
    assert list(target.iterdir()) == []


class _StopBeforeServing(Exception):
    pass


def _start(tmp_path: Path, monkeypatch, *extra: str):
    """Run `start` up to app creation; the server itself never starts."""
    import anygarden.app

    seen = {}

    def fake_create_app(config):
        seen["config"] = config
        raise _StopBeforeServing

    monkeypatch.setattr(anygarden.app, "create_app", fake_create_app)
    result = CliRunner().invoke(
        dispatch,
        ["start", "--data-dir", str(tmp_path), "--peer-port", "8451", *extra],
    )
    return result, seen.get("config")


@pytest.mark.req("FED-10")
def test_start_creates_credentials_and_prints_identity(tmp_path, monkeypatch):
    result, config = _start(tmp_path, monkeypatch)

    assert isinstance(result.exception, _StopBeforeServing), result.output
    identity = inspect_certificate((tmp_path / "peer" / "peer-cert.pem").read_text())
    assert "Created new peer credentials" in result.output
    assert identity.node_id in result.output
    assert identity.fingerprint in result.output
    assert config.peer_listen_port == 8451


@pytest.mark.req("FED-10")
def test_start_with_existing_pair_prints_identity_without_creating(
    tmp_path, monkeypatch
):
    cert, _ = create_credentials(tmp_path / "peer", str(uuid4()))
    result, _ = _start(tmp_path, monkeypatch)

    assert isinstance(result.exception, _StopBeforeServing), result.output
    assert "Created new peer credentials" not in result.output
    assert inspect_certificate(cert.read_text()).fingerprint in result.output


def test_start_accepts_and_validates_public_url(tmp_path, monkeypatch):
    result, config = _start(
        tmp_path, monkeypatch, "--peer-public-url", "https://alpha.example:8451"
    )
    assert isinstance(result.exception, _StopBeforeServing), result.output
    assert config.peer_public_url == "https://alpha.example:8451"

    bad, _ = _start(tmp_path, monkeypatch, "--peer-public-url", "http://alpha:8451")
    assert bad.exit_code != 0 and not isinstance(bad.exception, _StopBeforeServing)
    assert "--peer-public-url" in bad.output
