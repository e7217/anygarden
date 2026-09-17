"""Deployment artifact regression (task #57).

The systemd template must keep the operational contract documented in
docs/runbook/federation-two-node.md: explicit opt-in peer port, single worker,
fail-fast startup, restart only on failure, and a stop timeout that covers the
graceful drain.
"""

from pathlib import Path


def _unit() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "systemd"
        / "anygarden-node@.service"
    )
    return path.read_text()


def test_unit_starts_the_integrated_node_with_explicit_peer_port():
    unit = _unit()
    assert (
        "ExecStart=/usr/local/bin/anygarden start --data-dir /var/lib/anygarden/%i"
        in unit
    )
    assert "--peer-port 8451" in unit
    assert "WEB_CONCURRENCY=1" in unit


def test_unit_restarts_only_on_failure_and_allows_graceful_drain():
    unit = _unit()
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "TimeoutStopSec=90" in unit
    assert "Restart=always" not in unit


def test_unit_documents_the_credential_prerequisite():
    unit = _unit()
    # The pointer keeps the PR609 fail-closed contract visible to operators.
    assert "federation-two-node.md" in unit
    assert "peer credentials" in unit
