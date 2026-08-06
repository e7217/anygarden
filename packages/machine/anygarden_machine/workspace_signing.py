"""Machine-local Ed25519 signing for scoped workspace receipts."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from anygarden_machine.safefs import safe_write_text, secure_chmod

DEFAULT_SIGNING_KEY_PATH = Path.home() / ".anygarden" / "workspace-signing.key"
_RECEIPT_DOMAIN = b"anygarden-workspace-receipt-v1\0"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def canonical_receipt(payload: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in payload.items() if key != "signature"}
    return _RECEIPT_DOMAIN + json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class WorkspaceReceiptSigner:
    """Load or create an owner-only signing key for workspace receipts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_SIGNING_KEY_PATH
        self._private_key = self._load_or_create()

    def _load_or_create(self) -> Ed25519PrivateKey:
        if self.path.exists():
            encoded = self.path.read_text(encoding="ascii").strip()
            if not encoded.startswith("ed25519sk_"):
                raise ValueError("invalid workspace receipt signing key")
            raw = _decode(encoded.removeprefix("ed25519sk_"))
            if len(raw) != 32:
                raise ValueError("invalid workspace receipt signing key")
            secure_chmod(self.path, 0o600)
            return Ed25519PrivateKey.from_private_bytes(raw)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        secure_chmod(self.path.parent, 0o700)
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        safe_write_text(self.path, f"ed25519sk_{_encode(raw)}\n", mode=0o600)
        return private_key

    @property
    def public_key(self) -> str:
        raw = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return f"ed25519pk_{_encode(raw)}"

    def sign(self, payload: dict[str, Any]) -> str:
        return (
            f"ed25519sig_{_encode(self._private_key.sign(canonical_receipt(payload)))}"
        )
