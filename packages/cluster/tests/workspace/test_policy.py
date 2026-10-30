"""#938 — the workspace action policy table."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from anygarden.workspace.policy import ACTIONS, CreateRoomParams, capped_permission


def test_every_action_has_a_tier_and_hire_is_admin_only() -> None:
    assert {spec.tier for spec in ACTIONS.values()} <= {"allowed", "ask", "off"}
    assert ACTIONS["hire_agent"].tier == "ask" and ACTIONS["hire_agent"].admin_only
    for name in ("create_routine", "create_goal", "archive_room", "remove_room_member"):
        assert ACTIONS[name].tier == "ask"
    assert not ACTIONS["remove_room_member"].admin_only
    for name in ("create_room", "create_task", "pause_goal"):
        assert ACTIONS[name].tier == "allowed"


def test_shared_channels_are_not_an_action() -> None:
    assert not any("channel" in name for name in ACTIONS)


def test_create_room_needs_exactly_one_parent() -> None:
    with pytest.raises(ValidationError):
        CreateRoomParams(name="x")
    with pytest.raises(ValidationError):
        CreateRoomParams(name="x", parent_room_id="r", project_id="p")
    assert CreateRoomParams(name="x", parent_room_id="r").parent_room_id == "r"


@pytest.mark.parametrize(
    ("requested", "creator", "expected"),
    [
        (None, None, None),
        (None, "restricted", "restricted"),
        ("trusted", "standard", "standard"),
        ("trusted", None, "standard"),
        ("restricted", "trusted", "restricted"),
        ("trusted", "trusted", "trusted"),
    ],
)
def test_a_hired_agent_never_outranks_its_creator(requested, creator, expected) -> None:
    assert capped_permission(requested, creator) == expected
