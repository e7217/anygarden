"""#843 — pin a per-node root; the node renews its own TLS leaf."""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from anygarden.federation.certificates import (
    LEAF_LIFETIME,
    LEAF_RENEW_BEFORE,
    ROOT_LIFETIME,
    certificate_expires_at,
    create_credentials,
    create_root,
    inspect_certificate,
    issue_leaf,
    leaf_needs_renewal,
    pin_matches,
    read_leaf,
)
from anygarden.federation.errors import PeerError


def root_node(tmp_path, node_id=None, *, now=None):
    node_id = node_id or str(uuid4())
    directory = tmp_path / str(uuid4())
    create_root(directory, node_id, now=now)
    issue_leaf(directory, now=now)
    return node_id, directory


def anchor(directory):
    return (directory / "peer-root-cert.pem").read_text()


def leaf_identity(directory):
    cert_pem, _ = read_leaf(directory)
    return inspect_certificate(cert_pem)


def test_root_and_leaf_identify_the_same_node(tmp_path):
    node_id, d = root_node(tmp_path)

    root = inspect_certificate(anchor(d))
    leaf = leaf_identity(d)

    assert root.node_id == leaf.node_id == node_id
    assert root.root is True and leaf.root is False
    assert root.fingerprint != leaf.fingerprint
    assert stat.S_IMODE((d / "peer-root-key.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE((d / "peer-leaf.pem").stat().st_mode) == 0o600


def test_lifetimes(tmp_path):
    _, d = root_node(tmp_path)
    now = datetime.now(UTC)
    root_left = certificate_expires_at(anchor(d)) - now
    leaf_left = certificate_expires_at(read_leaf(d)[0]) - now
    assert ROOT_LIFETIME - timedelta(days=1) < root_left <= ROOT_LIFETIME
    assert LEAF_LIFETIME - timedelta(days=1) < leaf_left <= LEAF_LIFETIME


@pytest.mark.req("FED-15")
def test_a_renewed_leaf_still_matches_the_same_pin(tmp_path):
    _, d = root_node(tmp_path)
    pin = anchor(d)
    first = leaf_identity(d)

    issue_leaf(d)
    second = leaf_identity(d)

    assert first.fingerprint != second.fingerprint
    assert pin_matches(pin, first) and pin_matches(pin, second)


@pytest.mark.req("FED-16")
def test_a_leaf_from_another_nodes_root_does_not_match(tmp_path):
    # Root C signs a leaf claiming node B: TLS may accept C's chain, the pin of
    # B must not.
    b, b_dir = root_node(tmp_path)
    _, c_dir = root_node(tmp_path)
    forged_dir = tmp_path / "forged"
    forged_dir.mkdir()
    for name in ("peer-root-cert.pem", "peer-root-key.pem"):
        (forged_dir / name).write_bytes((c_dir / name).read_bytes())
    issue_leaf(forged_dir, node_id=b)
    forged = leaf_identity(forged_dir)

    assert forged.node_id == b
    assert pin_matches(anchor(b_dir), forged) is False


def test_a_leaf_naming_a_different_node_does_not_match_its_issuer(tmp_path):
    _, d = root_node(tmp_path)
    issue_leaf(d, node_id=str(uuid4()))
    assert pin_matches(anchor(d), leaf_identity(d)) is False


def test_the_pinned_certificate_itself_matches(tmp_path):
    # Local re-checks pass the identity of the stored pin itself.
    _, d = root_node(tmp_path)
    assert pin_matches(anchor(d), inspect_certificate(anchor(d)))


@pytest.mark.req("FED-17")
def test_legacy_self_signed_pins_keep_exact_matching(tmp_path):
    cert, _ = create_credentials(tmp_path / "legacy", str(uuid4()))
    legacy = inspect_certificate(cert.read_text())
    other, _ = create_credentials(tmp_path / "other", legacy.node_id)

    assert legacy.root is False
    assert pin_matches(cert.read_text(), legacy)
    # Same node ID, different key: a legacy pin is exact.
    assert not pin_matches(cert.read_text(), inspect_certificate(other.read_text()))


def test_root_must_not_be_overwritten(tmp_path):
    node_id, d = root_node(tmp_path)
    with pytest.raises(PeerError, match="CREDENTIALS_EXIST"):
        create_root(d, node_id)


def test_leaf_renewal_is_due_within_30_days_or_when_missing(tmp_path):
    _, d = root_node(tmp_path)
    now = datetime.now(UTC)
    assert leaf_needs_renewal(d, now=now) is False
    assert leaf_needs_renewal(
        d, now=now + LEAF_LIFETIME - LEAF_RENEW_BEFORE + timedelta(hours=1)
    )
    (d / "peer-leaf.pem").unlink()
    assert leaf_needs_renewal(d, now=now) is True


def test_leaf_not_issued_by_the_current_root_needs_renewal(tmp_path):
    node_id, d = root_node(tmp_path)
    _, other = root_node(tmp_path, node_id)
    (d / "peer-leaf.pem").write_bytes((other / "peer-leaf.pem").read_bytes())
    assert leaf_needs_renewal(d) is True


def test_presented_roots_are_not_accepted_as_a_tls_identity(tmp_path):
    import ssl

    from anygarden.federation.certificates import identity_from_tls

    _, d = root_node(tmp_path)

    class FakeSSL:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.verify_mode = ssl.CERT_REQUIRED

        def getpeercert(self, binary_form):
            return ssl.PEM_cert_to_DER_cert(anchor(d))

    with pytest.raises(PeerError):
        identity_from_tls(FakeSSL())


def test_root_without_cert_sign_or_with_deeper_path_is_denied(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    node_id = str(uuid4())
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
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=3), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier(f"urn:anygarden:node:{node_id}")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    with pytest.raises(PeerError, match="CERTIFICATE_DENIED"):
        inspect_certificate(pem)
