"""Pasteable machine connection codes (#848)."""

from __future__ import annotations

import base64
import json

import pytest

from anygarden.machine.link_code import (
    LINK_CODE_PREFIX,
    InvalidMachineConnection,
    decode_link_code,
    encode_link_code,
    machine_ws_url,
)

TOKEN = "mch_" + "b" * 43


def _raw(payload: object) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return LINK_CODE_PREFIX + body


def test_round_trip_builds_the_daemon_socket_url() -> None:
    code = encode_link_code("https://garden.test:8443/base/", "m-1", TOKEN, "Y remote")
    link = decode_link_code("  " + code + "\n")
    assert link.server == "https://garden.test:8443/base"
    assert link.machine_id == "m-1"
    assert link.token == TOKEN
    assert link.name == "Y remote"
    assert link.ws_url == "wss://garden.test:8443/base/ws/machines/m-1"
    assert TOKEN not in repr(link)


def test_name_defaults_to_the_machine_id() -> None:
    assert decode_link_code(encode_link_code("http://x", "m-2", TOKEN)).name == "m-2"


@pytest.mark.parametrize(
    "code",
    [
        "aginvite1.abc",
        LINK_CODE_PREFIX + "!!!",
        LINK_CODE_PREFIX + "A" * 5000,
        _raw({"v": 2, "server": "http://x", "machine_id": "m", "token": TOKEN}),
        _raw({"v": 1, "server": "http://x", "machine_id": "m"}),
        _raw({"v": 1, "server": "file:///x", "machine_id": "m", "token": TOKEN}),
        _raw({"v": 1, "server": "http://u:p@x", "machine_id": "m", "token": TOKEN}),
        _raw({"v": 1, "server": "http://x", "machine_id": "../m", "token": TOKEN}),
        _raw({"v": 1, "server": "http://x", "machine_id": "m", "token": "secret"}),
        _raw(["not", "an", "object"]),
    ],
)
def test_unusable_codes_are_rejected(code: str) -> None:
    with pytest.raises(InvalidMachineConnection):
        decode_link_code(code)


def test_ws_url_reports_which_field_is_wrong() -> None:
    with pytest.raises(InvalidMachineConnection) as server:
        machine_ws_url("http://[bad", "m")
    assert server.value.field == "server"
    with pytest.raises(InvalidMachineConnection) as machine:
        machine_ws_url("http://x", "m/1")
    assert machine.value.field == "machine_id"
