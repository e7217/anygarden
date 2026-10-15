"""Locate, load and (on first run only) create the node's peer credential pair.

`start --peer-port` creates the pair when the default ``<data-dir>/peer``
directory holds neither file: that is a first run, not a rotation. Anything
that could silently change an existing identity is refused instead — a
partial or unusable pair, or an explicitly configured directory that is
missing (most likely an unmounted volume).

An expired but otherwise intact pair is reported separately
(:class:`CredentialsExpired`) so the node can still boot with federation off
(#842); renewing it is an explicit operator step that peers must re-pin.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from anygarden.config import AnygardenSettings
from anygarden.federation.certificates import (
    CertificateIdentity,
    certificate_expires_at,
    create_credentials,
    inspect_certificate,
    is_expired,
)
from anygarden.federation.errors import PeerError

CERT_NAME = "peer-cert.pem"
KEY_NAME = "peer-key.pem"


class CredentialSetupError(Exception):
    """An operator-facing reason the credential pair cannot be used."""


class CredentialsExpired(CredentialSetupError):
    """The pair is intact but past its validity; federation must stay off."""

    def __init__(
        self, message: str, identity: CertificateIdentity, expired_at: datetime
    ):
        super().__init__(message)
        self.identity = identity
        self.expired_at = expired_at


def peer_directory(data_dir: Path, config: AnygardenSettings) -> Path:
    """The credential directory exactly as `start` and app startup resolve it."""
    peer_dir = (config.peer_credentials_dir or (data_dir / "peer")).absolute()
    if peer_dir.is_symlink():
        raise CredentialSetupError(
            f"Peer credential directory must not be a symlink: {peer_dir}"
        )
    return peer_dir


def local_node_id(data_dir: Path) -> str | None:
    """The node UUID recorded by the local node, if it has one."""
    identity_path = data_dir / "node-identity.json"
    if not identity_path.is_file():
        return None
    try:
        return str(UUID(str(json.loads(identity_path.read_text())["node_id"])))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CredentialSetupError(f"Cannot read node_id from {identity_path}") from exc


def ensure_peer_credentials(
    data_dir: Path, config: AnygardenSettings
) -> tuple[CertificateIdentity, bool]:
    """Return the pair's identity and whether this call created the pair."""
    data_dir = data_dir.absolute()
    peer_dir = peer_directory(data_dir, config)
    cert_path, key_path = peer_dir / CERT_NAME, peer_dir / KEY_NAME
    present = [p for p in (cert_path, key_path) if p.exists() or p.is_symlink()]
    if len(present) == 2:
        renew = (
            f"`anygarden federation init-credentials --force --data-dir {data_dir}` "
            "(peers must re-pin the new fingerprint)"
        )
        try:
            pem = cert_path.read_text()
            identity = inspect_certificate(pem, check_validity=False)
        except (PeerError, OSError, UnicodeError) as exc:
            code = exc.code if isinstance(exc, PeerError) else type(exc).__name__
            raise CredentialSetupError(
                f"Peer credentials under {peer_dir} are unusable ({code}); "
                f"replace them with {renew}"
            ) from exc
        if is_expired(pem):
            expired_at = certificate_expires_at(pem)
            raise CredentialsExpired(
                f"The peer certificate under {peer_dir} expired on "
                f"{expired_at:%Y-%m-%d}; renew it with {renew}",
                identity,
                expired_at,
            )
        try:
            return inspect_certificate(pem), False
        except PeerError as exc:  # not yet valid: a clock or file problem
            raise CredentialSetupError(
                f"Peer credentials under {peer_dir} are unusable ({exc.code}); "
                f"replace them with {renew}"
            ) from exc
    if present:
        raise CredentialSetupError(
            f"Peer credentials under {peer_dir} are incomplete (found only "
            f"{present[0].name}); restore the missing file or replace the pair with "
            f"`anygarden federation init-credentials --force --data-dir {data_dir}`"
        )
    if config.peer_credentials_dir is not None:
        raise CredentialSetupError(
            f"No peer credentials under ANYGARDEN_PEER_CREDENTIALS_DIR ({peer_dir}); "
            "check that the directory is mounted, or create the pair with "
            f"`anygarden federation init-credentials --data-dir {data_dir}`"
        )
    node_id = local_node_id(data_dir) or str(uuid4())
    try:
        cert_path, _ = create_credentials(peer_dir, node_id)
    except PeerError as exc:
        raise CredentialSetupError(
            f"Cannot create peer credentials: {exc.code}"
        ) from exc
    return inspect_certificate(cert_path.read_text()), True
