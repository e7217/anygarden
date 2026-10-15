"""#839 — connection cards and invite codes: one pasteable string per hop."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from anygarden.federation.certificates import create_credentials, inspect_certificate
from anygarden.federation.connection_codes import (
    CARD_PREFIX,
    INVITE_PREFIX,
    MAX_CODE_CHARS,
    decode_card,
    decode_invite,
    encode_card,
    encode_invite,
    requires_private,
    suggest_ips,
)
from anygarden.federation.errors import PeerError
from anygarden.federation.schemas import InviteBundle

pytestmark = pytest.mark.req("FED-11")


@pytest.fixture
def node(tmp_path):
    node_id = str(uuid4())
    cert, _ = create_credentials(tmp_path / "peer", node_id)
    return node_id, cert.read_text()


def _bundle(issuer_pem: str, issuer_id: str) -> InviteBundle:
    now = datetime.now(UTC)
    return InviteBundle(
        invite_id=uuid4(),
        issuer_node_id=issuer_id,
        issuer_certificate_pem=issuer_pem,
        intended_node_id=uuid4(),
        intended_fingerprint="ab" * 32,
        token="t" * 43,
        scopes=[
            {
                "channel_id": str(uuid4()),
                "actors": [
                    {
                        "node_id": str(uuid4()),
                        "kind": "human",
                        "principal_id": str(uuid4()),
                    }
                ],
                "capabilities": ["channel.read"],
                "role": "observer",
            }
        ],
        expires_at=now + timedelta(minutes=10),
        grant_expires_at=now + timedelta(days=1),
    )


def _raw(prefix: str, payload) -> str:
    data = json.dumps(payload).encode()
    return prefix + base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_card_round_trip_carries_identity(node):
    node_id, pem = node
    card = encode_card(node_id, pem, "https://alpha.example:8451")

    assert card.startswith(CARD_PREFIX)
    decoded, identity = decode_card(card)
    assert str(decoded.node_id) == node_id
    assert decoded.certificate_pem == pem
    assert decoded.url == "https://alpha.example:8451"
    assert identity == inspect_certificate(pem)


def test_card_tolerates_wrapped_paste(node):
    node_id, pem = node
    card = encode_card(node_id, pem, "https://alpha.example:8451")
    wrapped = "  " + "\n".join(card[i : i + 60] for i in range(0, len(card), 60)) + "\n"

    assert decode_card(wrapped)[0].url == "https://alpha.example:8451"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda card: "agpeer9." + card.split(".", 1)[1],  # unknown version
        lambda card: INVITE_PREFIX + card.split(".", 1)[1],  # wrong kind
        lambda card: card[:-6] + "!!!!!!",  # not base64url
        lambda card: CARD_PREFIX + "A" * MAX_CODE_CHARS,  # oversized
        lambda card: "",
    ],
)
def test_malformed_cards_are_refused(node, mutate):
    card = encode_card(node[0], node[1], "https://alpha.example:8451")
    with pytest.raises(PeerError) as exc:
        decode_card(mutate(card))
    assert exc.value.code == "CODE_INVALID"


def test_card_node_id_must_match_certificate(node):
    _, pem = node
    forged = _raw(
        CARD_PREFIX,
        {"node_id": str(uuid4()), "certificate_pem": pem, "url": "https://a.example"},
    )
    with pytest.raises(PeerError) as exc:
        decode_card(forged)
    assert exc.value.code == "IDENTITY_MISMATCH"


def test_card_rejects_unknown_fields_and_bad_urls(node):
    node_id, pem = node
    extra = _raw(
        CARD_PREFIX,
        {
            "node_id": node_id,
            "certificate_pem": pem,
            "url": "https://a.example",
            "approved_ips": ["203.0.113.1"],
        },
    )
    with pytest.raises(PeerError):
        decode_card(extra)
    with pytest.raises(PeerError):
        encode_card(node_id, pem, "http://a.example:8451")
    plain = _raw(
        CARD_PREFIX,
        {"node_id": node_id, "certificate_pem": pem, "url": "https://a.example/path"},
    )
    with pytest.raises(PeerError):
        decode_card(plain)


def test_invite_round_trip_keeps_the_secret_token(node):
    issuer_id, pem = node
    bundle = _bundle(pem, issuer_id)
    code = encode_invite(bundle, "https://alpha.example:8451")

    assert code.startswith(INVITE_PREFIX)
    decoded, identity = decode_invite(code)
    assert decoded.bundle.token.get_secret_value() == "t" * 43
    assert decoded.bundle.invite_id == bundle.invite_id
    assert decoded.issuer_url == "https://alpha.example:8451"
    assert identity.node_id == issuer_id


def test_invite_issuer_must_match_its_certificate(node):
    _, pem = node
    bundle = _bundle(pem, str(uuid4()))
    with pytest.raises(PeerError) as exc:
        decode_invite(encode_invite(bundle, "https://alpha.example:8451"))
    assert exc.value.code == "IDENTITY_MISMATCH"


async def test_literal_ip_hosts_suggest_themselves_without_dns():
    assert await suggest_ips("192.168.10.5") == ["192.168.10.5"]
    assert await suggest_ips("2001:db8::7") == ["2001:db8::7"]


async def test_hostnames_suggest_resolved_addresses(monkeypatch):
    import anygarden.federation.connection_codes as codes

    async def fake_resolve(host):
        assert host == "alpha.example"
        return ["203.0.113.4", "203.0.113.4", "10.0.0.9"]

    monkeypatch.setattr(codes, "_resolve", fake_resolve)
    assert await suggest_ips("alpha.example") == ["203.0.113.4", "10.0.0.9"]


async def test_unresolvable_hostnames_suggest_nothing(monkeypatch):
    import anygarden.federation.connection_codes as codes

    async def fake_resolve(host):
        raise OSError("no such host")

    monkeypatch.setattr(codes, "_resolve", fake_resolve)
    assert await suggest_ips("alpha.example") == []


def test_requires_private_flags_non_global_addresses():
    assert requires_private(["192.168.1.2"]) is True
    assert requires_private(["10.0.0.1", "203.0.113.4"]) is True
    assert requires_private(["8.8.8.8"]) is False
    assert requires_private([]) is False
