# /// script
# requires-python = ">=3.11"
# dependencies = ["jsonschema==4.23.0"]
# ///
"""Executable contract model, NOT product implementation or durability evidence.

Run: uv run --no-project contracts/federation/v1/check.py
Consumers may use schema files and scenarios.json without importing this model.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent
SCHEMAS = [
    json.loads((ROOT / name).read_text())
    for name in ("envelope.schema.json", "receipt.schema.json", "event.schema.json")
]
REGISTRY = Registry().with_resources(
    (schema["$id"], Resource.from_contents(schema)) for schema in SCHEMAS
)
VALIDATORS = [
    Draft202012Validator(s, registry=REGISTRY, format_checker=FormatChecker())
    for s in SCHEMAS
]


def canonical(value: dict) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def strict_load(raw: str) -> dict:
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out

    def invalid_constant(value):
        raise ValueError("non-JSON numeric constant")

    return json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=invalid_constant,
        parse_float=invalid_constant,
    )


class Model:
    """One channel's deterministic authority policy; no sockets/DB/processes."""

    def __init__(self, context: dict):
        self.context = copy.deepcopy(context)
        self.receipts = {}
        self.delegations = {}
        self.messages = {}
        self.tasks = {}
        self.events = []
        self.observations = []

    def apply(self, command: dict, transport_node: str) -> dict:
        def error(code):
            return {"code": code}

        if len(canonical(command).encode("utf-8")) > 65536:
            return error("INVALID_SCHEMA")
        if command.get("protocol_version") != 1:
            return error("VERSION_UNSUPPORTED")
        if not VALIDATORS[0].is_valid(command):
            return error("INVALID_SCHEMA")
        c = self.context
        sender = command["sender_node_id"]
        actor = command["actor"]
        if sender != transport_node or actor["node_id"] != sender:
            return error("IDENTITY_MISMATCH")
        if command["authority_node_id"] != c["authority_node_id"]:
            return error("WRONG_AUTHORITY")
        if command["channel_id"] != c["channel_id"]:
            return error("CHANNEL_DENIED")
        grant = c["grants"].get(sender)
        if (
            not grant
            or not grant["active"]
            or grant["expires_at"] <= c["now"]
            or command["grant_epoch"] != grant["epoch"]
            or c["archived"]
        ):
            return error("GRANT_DENIED")
        if actor not in grant["actors"]:
            return error("PRINCIPAL_DENIED")
        kind = command["kind"]
        capability = {
            "message.send": "message.send",
            "task.request": "task.request",
            "task.cancel": "task.cancel",
        }.get(kind, "task.execute")
        if capability not in grant["capabilities"]:
            return error("SCOPE_DENIED")
        if (
            kind not in ("message.send", "task.request", "task.cancel")
            and {"node_id": actor["node_id"], "agent_id": actor["principal_id"]}
            not in c["exports"]
        ):
            return error("EXECUTOR_DENIED")
        if (
            kind == "task.request"
            and command["payload"]["executor"] not in c["exports"]
        ):
            return error("EXECUTOR_DENIED")
        if not c["authority_online"]:
            return error("AUTHORITY_UNAVAILABLE")
        key = (
            sender,
            command["authority_node_id"],
            command["channel_id"],
            command["request_id"],
        )
        body = canonical(command)
        # Fresh authentication and authorization deliberately precede receipt reuse.
        old = self.receipts.get(key)
        if old:
            return (
                {"code": "DUPLICATE", "receipt": old[1]}
                if old[0] == body
                else error("ID_CONFLICT")
            )
        p = command["payload"]
        revision = 0
        if kind == "message.send":
            if p["message_id"] in self.messages:
                return error("MESSAGE_CONFLICT")
            root = p["thread_root_id"]
            if root is not None and (
                root not in self.messages
                or self.messages[root]["thread_root_id"] is not None
            ):
                return error("THREAD_DENIED")
            self.messages[p["message_id"]] = copy.deepcopy(p)
            state = "message_committed"
            process_state = "not_applicable"
        else:
            did = p["delegation_id"]
            existing = self.delegations.get(did)
            if kind == "task.request":
                if existing or p["task_id"] in self.tasks:
                    return error("CLAIM_CONFLICT")
                if p["expected_revision"] != 0:
                    return error("REVISION_CONFLICT")
                if p["source_message_id"] not in self.messages:
                    return error("SOURCE_DENIED")
                if c["task_sources"].get(p["task_id"]) != p["source_message_id"]:
                    return error("TASK_DENIED")
                executor = p["executor"]
                if executor not in c["exports"]:
                    return error("EXECUTOR_DENIED")
                state = "requested"
                record = {
                    "state": state,
                    "revision": 1,
                    "requester": actor,
                    "executor": executor,
                    "execution_id": None,
                    "process_state": "not_started",
                }
                self.tasks[p["task_id"]] = did
            else:
                if existing is None:
                    return error("DELEGATION_MISSING")
                record = copy.deepcopy(existing)
                if kind == "task.cancel":
                    if actor != record["requester"] and actor not in c["admins"]:
                        return error("ACTOR_DENIED")
                else:
                    executor_actor = {
                        "node_id": record["executor"]["node_id"],
                        "kind": "agent",
                        "principal_id": record["executor"]["agent_id"],
                    }
                    if actor != executor_actor:
                        return error("ACTOR_DENIED")
                    if record["executor"] not in c["exports"]:
                        return error("EXECUTOR_DENIED")
                if (
                    record["state"] in ("cancel_requested", "cancelled")
                    and kind == "task.result"
                ):
                    if p["execution_id"] != record["execution_id"]:
                        return error("EXECUTION_MISMATCH")
                    observation = {
                        "execution_id": p["execution_id"],
                        "reason": "LATE_RESULT_AFTER_CANCEL",
                    }
                    if observation not in self.observations:
                        self.observations.append(observation)
                    return error(
                        "CANCEL_PENDING"
                        if record["state"] == "cancel_requested"
                        else "TERMINAL"
                    )
                if p["expected_revision"] != record["revision"]:
                    return error("REVISION_CONFLICT")
                previous = record["state"]
                if previous in ("completed", "cancelled", "rejected"):
                    return error("TERMINAL")
                if previous == "unknown" and kind != "task.cancel":
                    return error("RECONCILE_REQUIRED")
                transitions = {
                    "task.accept": (("requested",), "accepted"),
                    "task.reject": (("requested",), "rejected"),
                    "task.started": (("accepted",), "running"),
                    "task.result": (("accepted", "running"), "completed"),
                    "task.cancel": (
                        ("requested", "accepted", "running", "unknown"),
                        "cancel_requested",
                    ),
                    "task.cancelled": (("cancel_requested",), "cancelled"),
                    "task.unknown": (
                        ("accepted", "running", "cancel_requested"),
                        "unknown",
                    ),
                }
                allowed, state = transitions[kind]
                if previous not in allowed:
                    return error("STATE_CONFLICT")
                if kind == "task.accept":
                    if not c["executor_policy_allows"]:
                        return error("LOCAL_POLICY_DENIED")
                    record["execution_id"] = p["execution_id"]
                elif "execution_id" in p:
                    # Before acceptance there is no execution binding; cancelled
                    # uses a new receipt ID solely for a not_started confirmation.
                    if record["execution_id"] is None:
                        if (
                            kind != "task.cancelled"
                            or p["process_state"] != "not_started"
                        ):
                            return error("EXECUTION_MISMATCH")
                    elif p["execution_id"] != record["execution_id"]:
                        return error("EXECUTION_MISMATCH")
                    if (
                        kind == "task.cancelled"
                        and p["process_state"] == "not_started"
                        and record["process_state"] == "running"
                    ):
                        return error("STATE_CONFLICT")
                if kind == "task.accept" or kind == "task.unknown":
                    record["process_state"] = "unknown"
                elif kind == "task.started":
                    record["process_state"] = "running"
                elif kind == "task.result":
                    record["process_state"] = "finished"
                elif kind == "task.cancelled":
                    record["process_state"] = p["process_state"]
                record.update(state=state, revision=record["revision"] + 1)
            self.delegations[did] = record
            revision = record["revision"]
            process_state = record["process_state"]
        seq = len(self.events) + 1
        event_id = str(uuid5(NAMESPACE_URL, canonical(list(key))))
        receipt = {
            "protocol_version": 1,
            "request_id": command["request_id"],
            "authority_node_id": command["authority_node_id"],
            "channel_id": command["channel_id"],
            "event_id": event_id,
            "seq": seq,
            "revision": revision,
            "state": state,
            "process_state": process_state,
        }
        event = {
            "protocol_version": 1,
            "event_id": event_id,
            "authority_node_id": command["authority_node_id"],
            "channel_id": command["channel_id"],
            "seq": seq,
            "request": copy.deepcopy(command),
            "receipt": receipt,
        }
        VALIDATORS[1].validate(receipt)
        VALIDATORS[2].validate(event)
        self.receipts[key] = (body, receipt)
        self.events.append(event)
        return {"code": "COMMITTED", "receipt": receipt}


class Follower:
    """In-memory ordered projection contract; ACK is only a modeled commit."""

    def __init__(self, authority: str, channel: str):
        self.authority = authority
        self.channel = channel
        self.cursor = 0
        self.events = {}
        self.sequences = {}

    def apply(self, event: dict, transport_node: str, can_read: bool) -> str:
        if not VALIDATORS[2].is_valid(event):
            return "INVALID_SCHEMA"
        if (
            transport_node != self.authority
            or event["authority_node_id"] != self.authority
        ):
            return "IDENTITY_MISMATCH"
        if event["channel_id"] != self.channel or not can_read:
            return "CHANNEL_DENIED"
        request, receipt = event["request"], event["receipt"]
        for key in ("authority_node_id", "channel_id"):
            if event[key] != request[key] or event[key] != receipt[key]:
                return "EVENT_INTEGRITY"
        if (
            event["event_id"] != receipt["event_id"]
            or event["seq"] != receipt["seq"]
            or request["request_id"] != receipt["request_id"]
        ):
            return "EVENT_INTEGRITY"
        body = canonical(event)
        old = self.events.get(event["event_id"])
        if old is not None:
            return "DUPLICATE" if old == body else "EVENT_INTEGRITY"
        seq = event["seq"]
        if seq in self.sequences:
            return "EVENT_INTEGRITY"
        if seq != self.cursor + 1:
            return "EVENT_GAP"
        self.events[event["event_id"]] = body
        self.sequences[seq] = event["event_id"]
        self.cursor = seq
        return "COMMITTED"


def check() -> None:
    for schema in SCHEMAS:
        Draft202012Validator.check_schema(schema)
    fixtures = strict_load((ROOT / "scenarios.json").read_text())
    steps = 0
    for case in fixtures["scenarios"]:
        model = Model(fixtures["context"])
        for step in case["steps"]:
            for path, value in step.get("context_updates", {}).items():
                target = model.context
                parts = path.split("/")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
            before = canonical(
                {
                    "receipts": list(model.receipts.values()),
                    "events": model.events,
                    "tasks": model.tasks,
                    "delegations": model.delegations,
                    "messages": model.messages,
                }
            )
            result = model.apply(step["command"], step["transport_node"])
            expected = step["expect"]
            assert result["code"] == expected["code"], (
                case["name"],
                steps,
                result,
                expected,
            )
            if "state" in expected:
                assert result["receipt"]["state"] == expected["state"], case["name"]
            if "observations" in expected:
                assert len(model.observations) == expected["observations"], case["name"]
            if "process_state" in expected:
                assert (
                    result["receipt"]["process_state"] == expected["process_state"]
                ), case["name"]
            if "events" in expected:
                assert len(model.events) == expected["events"], case["name"]
            if result["code"] != "COMMITTED":
                after = canonical(
                    {
                        "receipts": list(model.receipts.values()),
                        "events": model.events,
                        "tasks": model.tasks,
                        "delegations": model.delegations,
                        "messages": model.messages,
                    }
                )
                assert before == after, (
                    f"{case['name']}: rejection/duplicate mutated state"
                )
            steps += 1
    # Event vectors derive their bytes from the shared normal-completion fixture.
    authority = Model(fixtures["context"])
    for step in fixtures["scenarios"][0]["steps"]:
        authority.apply(step["command"], step["transport_node"])
    event_steps = 0
    for case in fixtures["event_scenarios"]:
        follower = Follower(
            authority.context["authority_node_id"], authority.context["channel_id"]
        )
        for action in case["steps"]:
            event = copy.deepcopy(authority.events[action["event_index"]])
            for path, value in action.get("updates", {}).items():
                target = event
                parts = path.split("/")
                for part in parts[:-1]:
                    target = target[part]
                target[parts[-1]] = value
            result = follower.apply(
                event,
                action.get("transport_node", follower.authority),
                action.get("can_read", True),
            )
            assert result == action["code"], (case["name"], result)
            assert follower.cursor == action["cursor"], case["name"]
            event_steps += 1
    for raw in ('{"a":1,"a":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1.0}'):
        try:
            strict_load(raw)
        except ValueError:
            pass
        else:
            raise AssertionError("accepted noncanonical input")
    print(
        f"Contract model: {len(fixtures['scenarios'])} scenarios / {steps} decisions passed"
    )
    print(
        f"Event model: {len(fixtures['event_scenarios'])} scenarios / {event_steps} decisions passed"
    )
    print(
        "Product integration, TLS, durable DB, session migration, process cancellation and provider: NOT RUN"
    )


if __name__ == "__main__":
    check()
