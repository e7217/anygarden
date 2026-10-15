"""Machine connection values and the pasteable machine link code (#848).

``agmachine1.`` wraps what ``anygarden machine connect`` asks for — the
server URL, the machine ID shown in the web UI and its one-time token — so a
node's web UI can accept a connection without a terminal. The code carries
the machine token: treat it like a password.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

LINK_CODE_PREFIX = "agmachine1."
MAX_LINK_CODE_CHARS = 4096
MACHINE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
_TOKEN = re.compile(r"mch_[A-Za-z0-9_-]{8,}")


class InvalidMachineConnection(ValueError):
    """A server URL, machine ID, token or link code that cannot be used."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


def machine_ws_url(server: str, machine_id: str) -> str:
    """The daemon socket URL for an HTTP(S) server base URL and machine ID."""
    try:
        parsed = urlsplit(server.strip())
        _ = parsed.port  # Validate malformed ports without connecting.
    except ValueError as exc:
        raise InvalidMachineConnection("Invalid server port", "server") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise InvalidMachineConnection(
            "Use an HTTP(S) base URL without credentials, query, or fragment",
            "server",
        )
    if not MACHINE_ID_PATTERN.fullmatch(machine_id):
        raise InvalidMachineConnection("Invalid machine ID", "machine_id")
    return urlunsplit(
        (
            "wss" if parsed.scheme == "https" else "ws",
            parsed.netloc,
            f"{parsed.path.rstrip('/')}/ws/machines/{machine_id}",
            "",
            "",
        )
    )


def check_machine_token(token: str) -> str:
    token = token.strip()
    if not _TOKEN.fullmatch(token):
        raise InvalidMachineConnection(
            "Invalid machine token format. Paste the token issued by the web UI.",
            "token",
        )
    return token


@dataclass(frozen=True)
class MachineLink:
    server: str
    machine_id: str
    token: str
    name: str
    ws_url: str

    def __repr__(self) -> str:  # Never print the token.
        return f"MachineLink(server={self.server!r}, machine_id={self.machine_id!r})"


def decode_link_code(code: str) -> MachineLink:
    code = code.strip()
    if not code.startswith(LINK_CODE_PREFIX) or len(code) > MAX_LINK_CODE_CHARS:
        raise InvalidMachineConnection("Not a machine connection code", "code")
    body = code[len(LINK_CODE_PREFIX) :]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data = json.loads(raw)
    except (binascii.Error, ValueError) as exc:
        raise InvalidMachineConnection("The connection code is damaged", "code") from exc
    if not isinstance(data, dict) or data.get("v") != 1:
        raise InvalidMachineConnection("Unsupported connection code version", "code")
    fields = {key: data.get(key) for key in ("server", "machine_id", "token")}
    if not all(isinstance(value, str) for value in fields.values()):
        raise InvalidMachineConnection("The connection code is incomplete", "code")
    name = data.get("name")
    name = name.strip()[:200] if isinstance(name, str) else ""
    server = fields["server"].strip().rstrip("/")
    ws_url = machine_ws_url(server, fields["machine_id"])
    token = check_machine_token(fields["token"])
    return MachineLink(
        server=server,
        machine_id=fields["machine_id"],
        token=token,
        name=name or fields["machine_id"],
        ws_url=ws_url,
    )


def encode_link_code(server: str, machine_id: str, token: str, name: str = "") -> str:
    """Inverse of :func:`decode_link_code` (the web UI builds the same string)."""
    payload = json.dumps(
        {"v": 1, "server": server, "machine_id": machine_id, "token": token, "name": name},
        separators=(",", ":"),
    ).encode()
    return LINK_CODE_PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip("=")
