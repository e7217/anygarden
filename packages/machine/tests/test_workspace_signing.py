"""Workspace receipt signing enrollment and scope regressions."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from anygarden_machine.workspace_signing import (
    WorkspaceReceiptSigner,
    canonical_receipt,
)


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def test_signing_key_is_owner_only_and_stable(tmp_path: Path) -> None:
    path = tmp_path / "machine" / "workspace-signing.key"
    first = WorkspaceReceiptSigner(path)
    second = WorkspaceReceiptSigner(path)

    assert first.public_key == second.public_key
    assert first.public_key.startswith("ed25519pk_")
    assert path.stat().st_mode & 0o777 == 0o600
    assert first.public_key not in path.read_text(encoding="ascii")


def test_signature_covers_complete_scoped_receipt(tmp_path: Path) -> None:
    signer = WorkspaceReceiptSigner(tmp_path / "workspace-signing.key")
    receipt = {
        "type": "workspace_attach_receipt",
        "attachment_id": "attachment-a",
        "workspace_id": "ws_opaque",
        "agent_id": "agent-a",
        "epoch": 3,
        "status": "denied",
        "reason": "consent_expired",
    }
    signature = signer.sign(receipt)
    public = Ed25519PublicKey.from_public_bytes(
        _decode(signer.public_key.removeprefix("ed25519pk_"))
    )
    public.verify(
        _decode(signature.removeprefix("ed25519sig_")),
        canonical_receipt(receipt),
    )

    receipt["epoch"] = 4
    with pytest.raises(InvalidSignature):
        public.verify(
            _decode(signature.removeprefix("ed25519sig_")),
            canonical_receipt(receipt),
        )
