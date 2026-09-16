"""Closed wire schemas and canonical bytes shared by every durable boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


class ChannelError(RuntimeError):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


# Command kinds that narrow the peer action below the default. Shared by
# ChannelService authorization and the #592 delegation reauthorization so the
# two current-grant boundaries cannot drift apart.
COMMAND_ACTIONS = {
    "message.send": "message.send",
    "task.request": "task.request",
    "task.cancel": "task.cancel",
}
DEFAULT_COMMAND_ACTION = "task.execute"


def command_action(kind: str) -> str:
    return COMMAND_ACTIONS.get(kind, DEFAULT_COMMAND_ACTION)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ChannelError("INVALID_SCHEMA", 400)
        result[key] = value
    return result


def _bad_number(value):
    raise ChannelError("INVALID_SCHEMA", 400)


def parse_json(raw: bytes | str) -> dict:
    if len(raw) > 131072:
        raise ChannelError("INVALID_SCHEMA", 400)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_float=_bad_number,
            parse_constant=_bad_number,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ChannelError("INVALID_SCHEMA", 400) from None
    if not isinstance(value, dict):
        raise ChannelError("INVALID_SCHEMA", 400)
    return value


def canonical(value: Any) -> str:
    def check(item):
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise ChannelError("INVALID_SCHEMA", 400) from None
            return
        if item is None or isinstance(item, bool):
            return
        if type(item) is int and abs(item) <= 9007199254740991:
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(isinstance(k, str) for k in item):
            for child in item.values():
                check(child)
            return
        raise ChannelError("INVALID_SCHEMA", 400)

    try:
        check(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (ValueError, RecursionError):
        raise ChannelError("INVALID_SCHEMA", 400) from None


_SCHEMAS = {
    name: json.loads((Path(__file__).parent / "contract" / filename).read_text())
    for name, filename in (
        ("command", "envelope.schema.json"),
        ("receipt", "receipt.schema.json"),
        ("event", "event.schema.json"),
        ("participant-event", "participant-event.schema.json"),
    )
}
_REGISTRY = Registry().with_resources(
    (s["$id"], Resource.from_contents(s)) for s in _SCHEMAS.values()
)
_VALIDATORS = {
    name: Draft202012Validator(
        schema, registry=_REGISTRY, format_checker=FormatChecker()
    )
    for name, schema in _SCHEMAS.items()
}


def validate(kind: str, value: dict) -> dict:
    canonical(value)
    if type(value.get("protocol_version")) is not int or value["protocol_version"] != 1:
        raise ChannelError("VERSION_UNSUPPORTED", 426)
    if not _VALIDATORS[kind].is_valid(value):
        raise ChannelError("INVALID_SCHEMA", 400)
    if kind == "event" and value.get("kind") == "participant.changed":
        if value["actor"]["node_id"] != value["authority_node_id"]:
            raise ChannelError("EVENT_INTEGRITY")
        return value
    if kind == "event":
        receipt, request = value["receipt"], value["request"]
        if request["actor"]["node_id"] != request["sender_node_id"]:
            raise ChannelError("EVENT_INTEGRITY")
        for field in ("event_id", "authority_node_id", "channel_id", "seq"):
            if value[field] != receipt[field]:
                raise ChannelError("EVENT_INTEGRITY")
        for field in ("request_id", "authority_node_id", "channel_id"):
            if request[field] != receipt[field]:
                raise ChannelError("EVENT_INTEGRITY")
        if request["kind"] == "message.send" and (
            receipt["state"] != "message_committed"
            or receipt["revision"] != 0
            or receipt["process_state"] != "not_applicable"
            or receipt["task_status"] is not None
        ):
            raise ChannelError("EVENT_INTEGRITY")
    return value


@dataclass(frozen=True)
class CommandEffect:
    revision: int
    state: str
    process_state: str
    task_status: str | None
