"""Pinned X.509 node identity, backed by verified mTLS proof of private-key possession."""

from __future__ import annotations

import hashlib
import os
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from anygarden.federation.errors import PeerError


@dataclass(frozen=True)
class CertificateIdentity:
    node_id: str
    fingerprint: str


def inspect_certificate(
    pem: str, *, now: datetime | None = None
) -> CertificateIdentity:
    try:
        if len(pem) > 16384 or pem.count("-----BEGIN CERTIFICATE-----") != 1:
            raise ValueError
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        now = now or datetime.now(UTC)
        if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
            raise ValueError
        sans = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        uris = sans.get_values_for_type(x509.UniformResourceIdentifier)
        if len(uris) != 1 or not uris[0].startswith("urn:anygarden:node:"):
            raise ValueError
        node_id = str(UUID(uris[0].removeprefix("urn:anygarden:node:")))
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if not {
            ExtendedKeyUsageOID.CLIENT_AUTH,
            ExtendedKeyUsageOID.SERVER_AUTH,
        }.issubset(eku):
            raise ValueError
        return CertificateIdentity(node_id, cert.fingerprint(hashes.SHA256()).hex())
    except (ValueError, TypeError, UnicodeError, x509.ExtensionNotFound):
        raise PeerError("CERTIFICATE_DENIED", 400) from None


def create_credentials(directory: Path, node_id: str) -> tuple[Path, Path]:
    """Explicit local setup only. Never overwrites keys or regenerates node identity."""
    node_id = str(UUID(node_id))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise PeerError("CREDENTIAL_PATH_DENIED", 400)
    key_path, cert_path = directory / "peer-key.pem", directory / "peer-cert.pem"
    if key_path.exists() or cert_path.exists():
        raise PeerError("CREDENTIALS_EXIST", 409)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:anygarden:node:{node_id}")]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    data = (
        (
            key_path,
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        ),
        (cert_path, cert.public_bytes(serialization.Encoding.PEM)),
    )
    for path, raw in data:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
    return cert_path, key_path


def tls_context(
    cert_path: Path, key_path: Path, trusted_pems: list[str], *, server: bool
) -> ssl.SSLContext:
    # No system CA pool, environment proxy, SSLKEYLOGFILE, or verification bypass.
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    if not server:
        # Node URI SAN + exact leaf pin replaces DNS identity, not chain validation.
        ctx.check_hostname = False
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    ctx.load_cert_chain(cert_path, key_path)
    for pem in trusted_pems:
        inspect_certificate(pem)
        ctx.load_verify_locations(cadata=pem)
    return ctx


def identity_from_tls(ssl_object: ssl.SSLObject) -> CertificateIdentity:
    if ssl_object.context.verify_mode != ssl.CERT_REQUIRED:
        raise PeerError("MTLS_REQUIRED", 401)
    der = ssl_object.getpeercert(binary_form=True)
    if not der:
        raise PeerError("MTLS_REQUIRED", 401)
    identity = inspect_certificate(ssl.DER_cert_to_PEM_cert(der))
    assert identity.fingerprint == hashlib.sha256(der).hexdigest()
    return identity
