"""Phase 5 machine-local workspace registration and consent regressions."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from anygarden_machine.workspace_registry import (
    WorkspaceRegistry,
    normalize_allowlist,
)


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return path


def test_registry_advertises_only_opaque_redacted_descriptor(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    registry_path = tmp_path / "machine" / "workspaces.json"
    registry = WorkspaceRegistry(registry_path)

    row = registry.register(
        root,
        label="docs checkout",
        max_mode="write",
        allowlist=["src", "README.md"],
        expires_in=timedelta(hours=1),
    )

    descriptor = registry.list_descriptors()[0]
    assert descriptor["workspace_id"] == row.workspace_id
    assert descriptor["workspace_id"].startswith("ws_")
    assert descriptor["label"] == "docs checkout"
    serialized = json.dumps(descriptor)
    assert str(root) not in serialized
    assert "canonical_root" not in serialized
    assert registry_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "value",
    ["../secret", "/etc/passwd", ".git/config", ".ssh/id_rsa", "a//b"],
)
def test_allowlist_rejects_traversal_repository_and_secret_paths(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_allowlist([value])


def test_registry_rejects_symlinked_workspace_root(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    link = tmp_path / "linked-repo"
    link.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        WorkspaceRegistry(tmp_path / "registry.json").register(
            link,
            label="linked",
            max_mode="read",
            allowlist=["README.md"],
            expires_in=timedelta(hours=1),
        )


def test_registry_rejects_path_shaped_label(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="host path"):
        WorkspaceRegistry(tmp_path / "registry.json").register(
            root,
            label=str(root),
            max_mode="read",
            allowlist=["README.md"],
            expires_in=timedelta(hours=1),
        )


def test_scoped_consent_is_one_time_and_policy_fingerprinted(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    row = registry.register(
        root,
        label="repo",
        max_mode="write",
        allowlist=["src"],
        expires_in=timedelta(hours=1),
    )
    proof = registry.issue_consent(
        row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        expires_in=timedelta(minutes=5),
    )
    assert proof.startswith("wcp_")
    assert proof not in (tmp_path / "registry.json").read_text(encoding="utf-8")

    denied, reason, _ = registry.verify_and_consume(
        workspace_id=row.workspace_id,
        agent_id="agent-b",
        room_id="room-a",
        mode="write",
        fingerprint=row.fingerprint,
        allowlist_digest=row.allowlist_hash,
        consent_proof=proof,
    )
    assert denied is False
    assert reason == "consent_scope_mismatch"

    accepted, reason, descriptor = registry.verify_and_consume(
        workspace_id=row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        fingerprint=row.fingerprint,
        allowlist_digest=row.allowlist_hash,
        consent_proof=proof,
    )
    assert accepted is True
    assert reason == "verified"
    assert descriptor is not None and "canonical_root" not in descriptor

    replayed, reason, _ = registry.verify_and_consume(
        workspace_id=row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        fingerprint=row.fingerprint,
        allowlist_digest=row.allowlist_hash,
        consent_proof=proof,
    )
    assert replayed is False
    assert reason == "consent_replayed"

    mismatch_proof = registry.issue_consent(
        row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        expires_in=timedelta(minutes=5),
    )
    mismatch, reason, _ = registry.verify_and_consume(
        workspace_id=row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        fingerprint="0" * 64,
        allowlist_digest=row.allowlist_hash,
        consent_proof=mismatch_proof,
    )
    assert mismatch is False
    assert reason == "fingerprint_mismatch"


def test_consent_fails_when_repository_identity_changes(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    registry = WorkspaceRegistry(tmp_path / "registry.json")
    row = registry.register(
        root,
        label="repo",
        max_mode="write",
        allowlist=["src"],
        expires_in=timedelta(hours=1),
    )
    proof = registry.issue_consent(
        row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        expires_in=timedelta(minutes=5),
    )
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "change"], check=True)

    accepted, reason, _ = registry.verify_and_consume(
        workspace_id=row.workspace_id,
        agent_id="agent-a",
        room_id="room-a",
        mode="write",
        fingerprint=row.fingerprint,
        allowlist_digest=row.allowlist_hash,
        consent_proof=proof,
    )
    assert accepted is False
    assert reason == "fingerprint_mismatch"
