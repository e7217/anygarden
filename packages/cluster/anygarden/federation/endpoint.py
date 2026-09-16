"""Admin-approved exact destination IPs; DNS never selects a socket destination."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

from anygarden.federation.errors import PeerError
from anygarden.federation.schemas import Endpoint


@dataclass(frozen=True)
class DialTarget:
    host: str
    port: int
    ips: tuple[str, ...]


def validate_endpoint(
    endpoint: Endpoint, *, allow_loopback: bool = False
) -> DialTarget:
    try:
        u = urlsplit(endpoint.url)
        if (
            u.scheme != "https"
            or not u.hostname
            or u.username
            or u.password
            or u.path not in ("", "/")
            or u.query
            or u.fragment
            or "%" in u.netloc
            or any(ord(c) < 33 or ord(c) > 126 for c in endpoint.url)
        ):
            raise ValueError
        host, port = u.hostname, u.port if u.port is not None else 443
        if not 1 <= port <= 65535 or "\\" in host:
            raise ValueError
        addresses = [ipaddress.ip_address(ip) for ip in endpoint.approved_ips]
        for ip in addresses:
            if (
                ip.is_unspecified
                or ip.is_multicast
                or ip.is_link_local
                or ip.is_reserved
                or getattr(ip, "ipv4_mapped", None) is not None
                or str(ip) in ("100.100.100.200", "168.63.129.16")
            ):
                raise ValueError
            if ip.is_loopback:
                if not allow_loopback:
                    raise ValueError
            elif not ip.is_global and not endpoint.allow_private:
                raise ValueError
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            # Strict DNS label form, although DNS is never used for dialing.
            import re

            if not re.fullmatch(
                r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", host
            ):
                raise ValueError
        else:
            if literal not in addresses:
                raise ValueError
        return DialTarget(host, port, tuple(str(ip) for ip in addresses))
    except (ValueError, TypeError):
        raise PeerError("ENDPOINT_DENIED", 400) from None
