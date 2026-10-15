"""FED-08 — operators create federation peer credentials from the CLI."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from click.testing import CliRunner

from anygarden.cli import dispatch
from anygarden.federation.certificates import inspect_certificate, tls_context

pytestmark = pytest.mark.req("FED-08")


def _init(*args: str):
    return CliRunner().invoke(
        dispatch, ["federation", "init-credentials", *args], catch_exceptions=False
    )


def _app_composes_peer_service(data_dir: Path) -> str | None:
    """Run the node's own startup credential loader against ``data_dir``."""
    from types import SimpleNamespace

    from anygarden.app import _compose_federation_services
    from anygarden.config import AnygardenSettings

    config = AnygardenSettings(_env_file=None)
    config.local_node_data_dir = data_dir
    app = SimpleNamespace(
        state=SimpleNamespace(config=config, session_factory=object())
    )
    _compose_federation_services(app)
    return getattr(app.state, "federation_node_id", None)


def test_creates_credentials_the_node_loads_at_startup(tmp_path):
    result = _init("--data-dir", str(tmp_path))
    assert result.exit_code == 0, result.output

    peer = tmp_path / "peer"
    cert, key = peer / "peer-cert.pem", peer / "peer-key.pem"
    identity = inspect_certificate(cert.read_text())
    UUID(identity.node_id)
    # Prints where it wrote and what the operator shares with peers.
    assert str(cert) in result.output and str(key) in result.output
    assert identity.node_id in result.output
    assert identity.fingerprint in result.output
    # Round trip: the startup loader composes peer services from them, and
    # the mTLS context accepts the pair.
    assert _app_composes_peer_service(tmp_path) == identity.node_id
    tls_context(cert, key, [], server=True)


def test_private_key_and_directory_are_owner_only(tmp_path):
    assert _init("--data-dir", str(tmp_path)).exit_code == 0
    peer = tmp_path / "peer"
    assert stat.S_IMODE((peer / "peer-key.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE(peer.stat().st_mode) == 0o700


def test_refuses_to_overwrite_existing_credentials(tmp_path):
    assert _init("--data-dir", str(tmp_path)).exit_code == 0
    peer = tmp_path / "peer"
    before = {p.name: p.read_bytes() for p in peer.iterdir()}

    result = _init("--data-dir", str(tmp_path))
    assert result.exit_code != 0
    assert "already exist" in result.output and "--force" in result.output
    assert {p.name: p.read_bytes() for p in peer.iterdir()} == before


def test_refuses_partial_existing_pair_without_force(tmp_path):
    peer = tmp_path / "peer"
    peer.mkdir(mode=0o700)
    (peer / "peer-cert.pem").write_text("stale")
    result = _init("--data-dir", str(tmp_path))
    assert result.exit_code != 0
    assert (peer / "peer-cert.pem").read_text() == "stale"
    assert not (peer / "peer-key.pem").exists()


def test_force_replaces_pair_but_keeps_node_identity(tmp_path):
    assert _init("--data-dir", str(tmp_path)).exit_code == 0
    cert = tmp_path / "peer" / "peer-cert.pem"
    old = inspect_certificate(cert.read_text())

    result = _init("--data-dir", str(tmp_path), "--force")
    assert result.exit_code == 0, result.output
    new = inspect_certificate(cert.read_text())
    assert new.node_id == old.node_id
    assert new.fingerprint != old.fingerprint
    assert stat.S_IMODE((tmp_path / "peer" / "peer-key.pem").stat().st_mode) == 0o600
    assert sorted(p.name for p in (tmp_path / "peer").iterdir()) == [
        "peer-cert.pem",
        "peer-key.pem",
    ]
    assert _app_composes_peer_service(tmp_path) == old.node_id


def test_reuses_local_node_identity_when_present(tmp_path):
    node_id = str(uuid4())
    (tmp_path / "node-identity.json").write_text(
        json.dumps({"node_id": node_id, "machine_id": str(uuid4())})
    )
    assert _init("--data-dir", str(tmp_path)).exit_code == 0
    cert = (tmp_path / "peer" / "peer-cert.pem").read_text()
    assert inspect_certificate(cert).node_id == node_id


def test_explicit_node_id_and_invalid_node_id(tmp_path):
    node_id = str(uuid4())
    assert _init("--data-dir", str(tmp_path), "--node-id", node_id).exit_code == 0
    cert = (tmp_path / "peer" / "peer-cert.pem").read_text()
    assert inspect_certificate(cert).node_id == node_id

    bad = _init("--data-dir", str(tmp_path / "other"), "--node-id", "not-a-uuid")
    assert bad.exit_code != 0
    assert not (tmp_path / "other" / "peer").exists()


def test_peer_port_error_names_the_command(tmp_path):
    # Only an explicitly configured (and missing) directory is refused; the
    # default directory gets a first-run pair (#839).
    result = CliRunner().invoke(
        dispatch,
        ["start", "--data-dir", str(tmp_path), "--peer-port", "8451"],
        env={"ANYGARDEN_PEER_CREDENTIALS_DIR": str(tmp_path / "unmounted")},
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "anygarden federation init-credentials" in result.output
