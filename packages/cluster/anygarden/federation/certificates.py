"""Pinned X.509 node identity, backed by verified mTLS proof of private-key possession.

Two pin kinds coexist (#843):

* root: a long-lived per-node CA (``peer-root-cert.pem``). Peers pin it; the
  node signs its own short-lived TLS leaf (``peer-leaf.pem``) and renews it
  without any peer action.
* legacy: a self-signed leaf (``peer-cert.pem``) pinned exactly, as before.

TLS verifies the chain against every pinned anchor; :func:`pin_matches` then
checks the presented leaf against *that sender's* pin, because Python 3.12's
``ssl`` cannot say which anchor verified it.
"""

from __future__ import annotations

import hashlib
import os
import ssl
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from anygarden.federation.errors import PeerError


@dataclass(frozen=True)
class CertificateIdentity:
    node_id: str
    fingerprint: str
    # #843: the DER (to check a leaf's issuer) and whether this is a node root.
    der: bytes = field(default=b"", compare=False, repr=False)
    root: bool = field(default=False, compare=False)


# #842: pins, not expiry, are the trust anchor and revocation is per peer, so
# a short lifetime mostly added manual re-pin work. New pairs last two years.
CERTIFICATE_LIFETIME = timedelta(days=730)
EXPIRY_WARNING = timedelta(days=30)
# #843 root pin: the root is long-lived, the leaf is renewed by the node.
ROOT_LIFETIME = timedelta(days=3650)
LEAF_LIFETIME = timedelta(days=90)
LEAF_RENEW_BEFORE = timedelta(days=30)
ROOT_CERT, ROOT_KEY, LEAF = "peer-root-cert.pem", "peer-root-key.pem", "peer-leaf.pem"
NODE_URI = "urn:anygarden:node:"


def inspect_certificate(
    pem: str, *, now: datetime | None = None, check_validity: bool = True
) -> CertificateIdentity:
    """Identity of a node certificate.

    ``check_validity=False`` skips only the validity window, so an expired
    certificate can still name the node it belonged to (renewal, diagnosis).
    It is never a trust decision.
    """
    try:
        if len(pem) > 16384 or pem.count("-----BEGIN CERTIFICATE-----") != 1:
            raise ValueError
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        now = now or datetime.now(UTC)
        if check_validity and not (
            cert.not_valid_before_utc <= now < cert.not_valid_after_utc
        ):
            raise ValueError
        sans = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        uris = sans.get_values_for_type(x509.UniformResourceIdentifier)
        if len(uris) != 1 or not uris[0].startswith(NODE_URI):
            raise ValueError
        node_id = str(UUID(uris[0].removeprefix(NODE_URI)))
        basic = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        if basic.ca:
            # A node root signs only that node's leaves: no intermediates.
            usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
            if basic.path_length != 0 or not usage.key_cert_sign:
                raise ValueError
        else:
            eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
            if not {
                ExtendedKeyUsageOID.CLIENT_AUTH,
                ExtendedKeyUsageOID.SERVER_AUTH,
            }.issubset(eku):
                raise ValueError
        return CertificateIdentity(
            node_id,
            cert.fingerprint(hashes.SHA256()).hex(),
            der=cert.public_bytes(serialization.Encoding.DER),
            root=basic.ca,
        )
    except (ValueError, TypeError, UnicodeError, x509.ExtensionNotFound):
        raise PeerError("CERTIFICATE_DENIED", 400) from None


def certificate_expires_at(pem: str) -> datetime:
    """``notAfter`` of a certificate already accepted by inspect_certificate."""
    return x509.load_pem_x509_certificate(pem.encode("ascii")).not_valid_after_utc


def is_expired(pem: str, *, now: datetime | None = None) -> bool:
    return certificate_expires_at(pem) <= (now or datetime.now(UTC))


def pin_matches(pin_pem: str, presented: CertificateIdentity) -> bool:
    """Whether ``presented`` is the pinned certificate or a leaf it issued.

    For a root pin the leaf must name the same node and be signed directly by
    that root; a legacy pin matches only itself. Validity of the pin is the
    caller's check (``inspect_certificate``).
    """
    pin = inspect_certificate(pin_pem, check_validity=False)
    if presented.fingerprint == pin.fingerprint:
        return True
    if not pin.root or presented.root or presented.node_id != pin.node_id:
        return False
    if not presented.der:
        return False
    issuer = x509.load_der_x509_certificate(pin.der)
    try:
        x509.load_der_x509_certificate(presented.der).verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature):
        return False
    return True


def _node_name(
    node_id: str, *, root: bool = False
) -> tuple[x509.Name, x509.SubjectAlternativeName]:
    # Root and leaf subjects must differ: a leaf whose subject equals its
    # issuer is treated by OpenSSL as self-signed and fails chain building.
    common_name = f"{node_id} root" if root else node_id
    return (
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]),
        x509.SubjectAlternativeName(
            [x509.UniformResourceIdentifier(f"{NODE_URI}{node_id}")]
        ),
    )


def _key_pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _write_exclusive(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())


def _credential_dir(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise PeerError("CREDENTIAL_PATH_DENIED", 400)


def create_root(
    directory: Path,
    node_id: str,
    *,
    now: datetime | None = None,
    lifetime: timedelta = ROOT_LIFETIME,
) -> Path:
    """Write the node root CA. Never overwrites an existing root."""
    node_id = str(UUID(node_id))
    _credential_dir(directory)
    cert_path, key_path = directory / ROOT_CERT, directory / ROOT_KEY
    if cert_path.exists() or key_path.exists():
        raise PeerError("CREDENTIALS_EXIST", 409)
    key = ec.generate_private_key(ec.SECP256R1())
    name, san = _node_name(node_id, root=True)
    now = now or datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + lifetime)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(san, critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    _write_exclusive(key_path, _key_pem(key))
    _write_exclusive(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    return cert_path


def issue_leaf(
    directory: Path,
    *,
    now: datetime | None = None,
    lifetime: timedelta = LEAF_LIFETIME,
    node_id: str | None = None,
) -> Path:
    """Sign a fresh TLS leaf with the node root and swap it in atomically.

    Certificate and key share one file, so a reader never sees a new
    certificate with an old key. ``node_id`` exists only for forgery tests.
    """
    root_pem = (directory / ROOT_CERT).read_bytes()
    root = x509.load_pem_x509_certificate(root_pem)
    root_key = serialization.load_pem_private_key(
        (directory / ROOT_KEY).read_bytes(), password=None
    )
    node_id = (
        node_id or inspect_certificate(root_pem.decode(), check_validity=False).node_id
    )
    key = ec.generate_private_key(ec.SECP256R1())
    name, san = _node_name(node_id)
    now = now or datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(min(now + lifetime, root.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(san, critical=False)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH]
            ),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )
    fd, staging = tempfile.mkstemp(prefix=".leaf-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(cert.public_bytes(serialization.Encoding.PEM) + _key_pem(key))
            out.flush()
            os.fsync(out.fileno())
        os.chmod(staging, 0o600)
        os.replace(staging, directory / LEAF)
    except BaseException:
        Path(staging).unlink(missing_ok=True)
        raise
    return directory / LEAF


def read_leaf(directory: Path) -> tuple[str, str]:
    """``(certificate_pem, key_pem)`` from the combined leaf file."""
    raw = (directory / LEAF).read_text()
    marker = "-----END CERTIFICATE-----"
    cut = raw.index(marker) + len(marker)
    return raw[:cut].strip() + "\n", raw[cut:].strip() + "\n"


def leaf_needs_renewal(directory: Path, *, now: datetime | None = None) -> bool:
    """Missing, unusable, not from the current root, or within 30 days of expiry."""
    now = now or datetime.now(UTC)
    try:
        cert_pem, _ = read_leaf(directory)
        leaf = inspect_certificate(cert_pem, now=now)
        if not pin_matches((directory / ROOT_CERT).read_text(), leaf):
            return True
        return certificate_expires_at(cert_pem) - now <= LEAF_RENEW_BEFORE
    except (OSError, ValueError, PeerError):
        return True


def create_credentials(
    directory: Path,
    node_id: str,
    *,
    now: datetime | None = None,
    lifetime: timedelta = CERTIFICATE_LIFETIME,
) -> tuple[Path, Path]:
    """Write a fresh pair. Never overwrites keys or regenerates node identity.

    ``now`` only exists so tests can create expired or expiring pairs.
    """
    node_id = str(UUID(node_id))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise PeerError("CREDENTIAL_PATH_DENIED", 400)
    key_path, cert_path = directory / "peer-key.pem", directory / "peer-cert.pem"
    if key_path.exists() or cert_path.exists():
        raise PeerError("CREDENTIALS_EXIST", 409)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    now = now or datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + lifetime)
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
    if identity.root:
        # A node presents its leaf; a root is only ever a pin.
        raise PeerError("CERTIFICATE_DENIED", 400)
    return identity
