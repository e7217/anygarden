"""Data-only participant control model. No product routes or authorization code."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


class ParticipantModel:
    """Local authority admin mutation sharing the ordinary channel event stream."""

    def __init__(self, authority, validator, canonical):
        self.authority = authority
        self.validator = validator
        self.canonical = canonical
        self.participants = {}
        self.operations = {}

    def apply(self, change, actor, local_admin, publication_allowed):
        c = self.authority.context
        # These inputs represent current local authentication and policy, not
        # caller-supplied admin flags accepted by a product HTTP handler.
        if actor["node_id"] != c["authority_node_id"] or not local_admin:
            return "ADMIN_REQUIRED"
        if c["archived"] or not c["authority_online"]:
            return "CHANNEL_UNAVAILABLE"
        if change["active"] and not publication_allowed:
            return "PRINCIPAL_DENIED"
        key = change["operation_id"]
        body = self.canonical({"change": change, "actor": actor})
        old = self.operations.get(key)
        if old:
            return "DUPLICATE" if old == body else "ID_CONFLICT"
        principal = self.canonical(change["principal"])
        previous = self.participants.get(principal)
        revision = previous["revision"] if previous else 0
        if change["expected_revision"] != revision:
            return "REVISION_CONFLICT"
        event = {
            "protocol_version": 1,
            "event_id": str(
                uuid5(
                    NAMESPACE_URL,
                    self.canonical(
                        [c["authority_node_id"], c["channel_id"], "participant", key]
                    ),
                )
            ),
            "authority_node_id": c["authority_node_id"],
            "channel_id": c["channel_id"],
            "seq": len(self.authority.events) + 1,
            "kind": "participant.changed",
            "actor": actor,
            "principal": change["principal"],
            "role": change["role"],
            "active": change["active"],
            "revision": revision + 1,
        }
        if not self.validator.is_valid(event):
            return "INVALID_SCHEMA"
        self.participants[principal] = {
            k: event[k] for k in ("role", "active", "revision")
        }
        self.operations[key] = body
        self.authority.events.append(copy.deepcopy(event))
        return "COMMITTED"


def check_participants(fixtures, model_class, follower_class, validator, canonical):
    cases = json.loads(
        (Path(__file__).parent / "participant-scenarios.json").read_text()
    )
    count = 0
    for case in cases:
        model = model_class(fixtures["context"])
        control = ParticipantModel(model, validator, canonical)
        follower = follower_class(
            model.context["authority_node_id"], model.context["channel_id"]
        )
        grants = canonical(model.context["grants"])
        for step in case["steps"]:
            before = canonical(
                [
                    control.participants,
                    control.operations,
                    model.events,
                    follower.participants,
                    follower.events,
                    follower.cursor,
                ]
            )
            if step["op"] == "change":
                result = control.apply(
                    step["change"],
                    step["actor"],
                    step["local_admin"],
                    step["publication_allowed"],
                )
            elif step["op"] == "command":
                command = step["command"]
                result = model.apply(command, command["sender_node_id"])["code"]
            else:
                event = copy.deepcopy(model.events[step["event_index"]])
                for path, value in step.get("updates", {}).items():
                    target = event
                    parts = path.split("/")
                    for part in parts[:-1]:
                        target = target[part]
                    target[parts[-1]] = value
                result = follower.apply(
                    event,
                    step.get("transport_node", follower.authority),
                    step.get("can_read", True),
                )
            assert result == step["expect"], (case["name"], result, step["expect"])
            if result != "COMMITTED":
                after = canonical(
                    [
                        control.participants,
                        control.operations,
                        model.events,
                        follower.participants,
                        follower.events,
                        follower.cursor,
                    ]
                )
                assert before == after, (
                    case["name"],
                    "rejection/duplicate mutated state",
                )
            if "cursor" in step:
                assert follower.cursor == step["cursor"], case["name"]
            if "participants" in step:
                assert list(follower.participants.values()) == step["participants"], (
                    case["name"]
                )
            assert canonical(model.context["grants"]) == grants, (
                "participant event changed a grant"
            )
            count += 1
    print(f"Participant model: {len(cases)} scenarios / {count} decisions passed")
