"""Locate, load and (on first run only) create the node's peer credential pair.

`start --peer-port` creates credentials when the default ``<data-dir>/peer``
directory holds none: that is a first run, not a rotation. New nodes get a
root layout (#843): a pinned node root plus a TLS leaf the node renews itself.
Legacy self-signed pairs keep working. Anything
that could silently change an existing identity is refused instead — a
partial or unusable pair, or an explicitly configured directory that is
missing (most likely an unmounted volume).

An expired but otherwise intact pair is reported separately
(:class:`CredentialsExpired`) so the node can still boot with federation off
(#842); renewing it is an explicit operator step that peers must re-pin.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from anygarden.config import AnygardenSettings
from anygarden.federation.certificates import (
    LEAF,
    ROOT_CERT,
    ROOT_KEY,
    CertificateIdentity,
    certificate_expires_at,
    create_root,
    inspect_certificate,
    is_expired,
    issue_leaf,
    leaf_needs_renewal,
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


@dataclass(frozen=True)
class PeerCredentialFiles:
    """Where a node's credentials live (#843).

    ``anchor_path`` is what peers pin; ``cert_path``/``key_path`` are what the
    node presents in TLS (the same combined leaf file for a root layout).
    """

    kind: Literal["root", "legacy"]
    anchor_path: Path
    cert_path: Path
    key_path: Path


def _present(peer_dir: Path, *names: str) -> list[Path]:
    paths = [peer_dir / name for name in names]
    return [p for p in paths if p.exists() or p.is_symlink()]


def locate_credentials(peer_dir: Path) -> PeerCredentialFiles | None:
    """The credential layout in ``peer_dir``; None when it holds none.

    A root layout wins over a legacy pair. Half of either is an error: it is
    never completed or replaced automatically.
    """
    roots = _present(peer_dir, ROOT_CERT, ROOT_KEY)
    if len(roots) == 2:
        leaf = peer_dir / LEAF
        return PeerCredentialFiles("root", peer_dir / ROOT_CERT, leaf, leaf)
    legacy = _present(peer_dir, CERT_NAME, KEY_NAME)
    for found in (roots, legacy):
        if len(found) == 1:
            raise CredentialSetupError(
                f"Peer credentials under {peer_dir} are incomplete (found only "
                f"{found[0].name})"
            )
    if len(legacy) == 2:
        return PeerCredentialFiles(
            "legacy", peer_dir / CERT_NAME, peer_dir / CERT_NAME, peer_dir / KEY_NAME
        )
    return None


def create_root_layout(peer_dir: Path, node_id: str) -> Path:
    """First-run credentials: a node root plus its first leaf."""
    cert = create_root(peer_dir, node_id)
    issue_leaf(peer_dir)
    return cert


def ensure_peer_credentials(
    data_dir: Path, config: AnygardenSettings
) -> tuple[CertificateIdentity, bool]:
    """Return the pinned identity and whether this call created credentials.

    A root layout also gets a fresh leaf when its leaf is due (#843).
    """
    data_dir = data_dir.absolute()
    peer_dir = peer_directory(data_dir, config)
    renew = (
        f"`anygarden federation init-credentials --force --data-dir {data_dir}` "
        "(peers must re-pin the new fingerprint)"
    )
    try:
        files = locate_credentials(peer_dir)
    except CredentialSetupError as exc:
        raise CredentialSetupError(
            f"{exc}; restore the missing file or replace the credentials with {renew}"
        ) from exc
    if files is None:
        if config.peer_credentials_dir is not None:
            raise CredentialSetupError(
                f"No peer credentials under ANYGARDEN_PEER_CREDENTIALS_DIR "
                f"({peer_dir}); check that the directory is mounted, or create "
                f"them with `anygarden federation init-credentials --data-dir "
                f"{data_dir}`"
            )
        node_id = local_node_id(data_dir) or str(uuid4())
        try:
            cert_path = create_root_layout(peer_dir, node_id)
        except PeerError as exc:
            raise CredentialSetupError(
                f"Cannot create peer credentials: {exc.code}"
            ) from exc
        return inspect_certificate(cert_path.read_text()), True
    try:
        pem = files.anchor_path.read_text()
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
        identity = inspect_certificate(pem)
    except PeerError as exc:  # not yet valid: a clock or file problem
        raise CredentialSetupError(
            f"Peer credentials under {peer_dir} are unusable ({exc.code}); "
            f"replace them with {renew}"
        ) from exc
    if files.kind == "root" and leaf_needs_renewal(peer_dir):
        issue_leaf(peer_dir)
    return identity, False
