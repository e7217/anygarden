"""Protocol versioning helpers."""

from __future__ import annotations

PROTOCOL_VERSION = "v1"
SUBPROTOCOL = "anygarden.v1"


def build_subprotocols(token: str) -> list[str]:
    """Return the WebSocket subprotocol list for authentication.

    The server expects: ``Sec-WebSocket-Protocol: anygarden.v1, bearer.<token>``
    """
    return [SUBPROTOCOL, f"bearer.{token}"]


def is_compatible(server_version: str) -> bool:
    """Check whether the SDK is compatible with the server protocol version."""
    return server_version == PROTOCOL_VERSION
