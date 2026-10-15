"""Pasteable strings admins exchange out of band to connect two nodes (#839).

A connection card (``agpeer1.``) carries a node's ID, certificate and the URL
peers dial. An invite code (``aginvite1.``) wraps an existing
:class:`InviteBundle` with the issuer's URL. Both are only a transport for
values the admin APIs already accept; the peer-to-peer protocol is unchanged.

The URL and the suggested IPs are prefill only. Dialing is still authorized
by the receiving admin's explicit ``approved_ips`` and ``allow_private``.
An invite code contains the invite's secret token.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import socket
from uuid import UUID

from pydantic import ValidationError, field_validator

from anygarden.federation.certificates import CertificateIdentity, inspect_certificate
from anygarden.federation.endpoint import split_peer_url
from anygarden.federation.errors import PeerError
from anygarden.federation.schemas import ClosedModel, InviteBundle

CARD_PREFIX = "agpeer1."
INVITE_PREFIX = "aginvite1."
MAX_CODE_CHARS = 32768


def check_peer_url(url: str) -> str:
    try:
        split_peer_url(url)
    except ValueError:
        raise PeerError("PUBLIC_URL_INVALID", 400) from None
    return url


class ConnectionCard(ClosedModel):
    node_id: UUID
    certificate_pem: str
    url: str

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        split_peer_url(value)
        return value


class InviteCode(ClosedModel):
    bundle: InviteBundle
    issuer_url: str

    @field_validator("issuer_url")
    @classmethod
    def _url(cls, value: str) -> str:
        split_peer_url(value)
        return value


def _encode(prefix: str, payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return prefix + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(prefix: str, text: str) -> object:
    compact = "".join(text.split())
    if not compact.startswith(prefix) or len(compact) > MAX_CODE_CHARS:
        raise PeerError("CODE_INVALID", 400)
    body = compact[len(prefix) :]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        return json.loads(raw)
    except (binascii.Error, ValueError):
        raise PeerError("CODE_INVALID", 400) from None


def _identity(pem: str, node_id: UUID) -> CertificateIdentity:
    identity = inspect_certificate(pem)
    if identity.node_id != str(node_id):
        raise PeerError("IDENTITY_MISMATCH", 400)
    return identity


def encode_card(node_id: str, certificate_pem: str, url: str) -> str:
    card = ConnectionCard(
        node_id=node_id, certificate_pem=certificate_pem, url=check_peer_url(url)
    )
    return _encode(CARD_PREFIX, card.model_dump(mode="json"))


def decode_card(text: str) -> tuple[ConnectionCard, CertificateIdentity]:
    try:
        card = ConnectionCard.model_validate(_decode(CARD_PREFIX, text))
    except ValidationError:
        raise PeerError("CODE_INVALID", 400) from None
    return card, _identity(card.certificate_pem, card.node_id)


def encode_invite(bundle: InviteBundle, issuer_url: str) -> str:
    payload = bundle.model_dump(mode="json")
    payload["token"] = bundle.token.get_secret_value()
    return _encode(
        INVITE_PREFIX, {"bundle": payload, "issuer_url": check_peer_url(issuer_url)}
    )


def decode_invite(text: str) -> tuple[InviteCode, CertificateIdentity]:
    try:
        code = InviteCode.model_validate(_decode(INVITE_PREFIX, text))
    except ValidationError:
        raise PeerError("CODE_INVALID", 400) from None
    bundle = code.bundle
    return code, _identity(bundle.issuer_certificate_pem, bundle.issuer_node_id)


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, None, type=socket.SOCK_STREAM
    )
    return [info[4][0] for info in infos]


async def suggest_ips(host: str) -> list[str]:
    """Addresses to prefill for admin approval. Never used to dial."""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    try:
        resolved = await asyncio.wait_for(_resolve(host), timeout=3)
    except (OSError, TimeoutError):
        return []
    return list(dict.fromkeys(resolved))


def requires_private(ips: list[str]) -> bool:
    """Whether approving these addresses needs ``allow_private``."""
    addresses = [ipaddress.ip_address(ip) for ip in ips]
    return any(not ip.is_global and not ip.is_loopback for ip in addresses)
