"""What an agent may do to the workspace, and how (#938).

Every ``workspace_action`` names one entry of :data:`ACTIONS`. The entry
fixes its tier:

- ``allowed`` — runs at once, with the rights of the person whose message
  started the agent's turn;
- ``ask`` — held as a :class:`WorkspaceActionRequest`; a person approves
  it in the room and it then runs once, with the approver's rights;
- ``off`` — refused.

``admin_only`` marks actions whose REST route is admin-only
(``get_admin_identity``). The executor calls route functions directly,
which skips their ``Depends`` guards, so this flag is what enforces it.

The table also records behaviour that already runs without approval, so
every self-directed change is decided in one place: a goal's (campaign's)
retrospective rewrites its agent's AGENTS.md and skills without a
person's approval (#930 decision 6). It is restorable from the
instructions history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Tier = Literal["allowed", "ask", "off"]

PERMISSION_LEVELS = ("restricted", "standard", "trusted")
"""Agent permission tiers, weakest first. ``None`` counts as ``standard``."""

APPROVAL_TTL_SECONDS = 60 * 60


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRoomParams(_Params):
    """A sub-room of ``parent_room_id``, or a top-level room of ``project_id``."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    parent_room_id: str | None = None
    project_id: str | None = None
    member_participant_ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _one_parent(self) -> CreateRoomParams:
        if (self.parent_room_id is None) == (self.project_id is None):
            raise ValueError("give exactly one of parent_room_id or project_id")
        if self.project_id is not None and self.member_participant_ids:
            raise ValueError("member_participant_ids applies to a sub-room only")
        return self


class AddRoomMemberParams(_Params):
    room_id: str
    agent_id: str


class RemoveRoomMemberParams(_Params):
    room_id: str
    agent_id: str


class CreateTaskParams(_Params):
    room_id: str
    title: str = Field(min_length=1, max_length=500)
    spec: str | None = Field(default=None, max_length=100000)
    assignee_participant_id: str | None = None


class ReassignTaskParams(_Params):
    task_id: str
    assignee_participant_id: str


class RoutineRefParams(_Params):
    routine_id: str


class GoalRefParams(_Params):
    goal_id: str


class CreateRoutineParams(_Params):
    """A recurring routine (code: ``Goal``) for ``agent_id``."""

    agent_id: str
    title: str = Field(min_length=1, max_length=500)
    spec: str = Field(min_length=1, max_length=100000)
    trigger_type: Literal["cron", "interval", "manual"]
    trigger_config: dict = Field(default_factory=dict)
    report_room_id: str
    materialize: Literal["full", "interesting_only"] = "interesting_only"


class CreateGoalParams(_Params):
    """A metric goal (code: ``Campaign``) in ``room_id``; ``spec`` is the
    ``POST /rooms/{id}/campaigns`` body."""

    room_id: str
    spec: dict


class ArchiveRoomParams(_Params):
    room_id: str


class HireAgentParams(_Params):
    name: str = Field(min_length=1, max_length=100)
    engine: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=200)
    agents_md: str | None = Field(default=None, max_length=100000)
    machine_id: str | None = None
    permission_level: Literal["restricted", "standard", "trusted"] | None = None
    room_ids: list[str] = Field(default_factory=list, max_length=20)


@dataclass(frozen=True)
class ActionSpec:
    name: str
    tier: Tier
    params: type[_Params]
    summary: str
    admin_only: bool = False


ACTIONS: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        ActionSpec("create_room", "allowed", CreateRoomParams,
                   "Create a sub-room (parent_room_id) or a project room (project_id)."),
        ActionSpec("add_room_member", "allowed", AddRoomMemberParams, "Add an agent to a room."),
        ActionSpec("remove_room_member", "ask", RemoveRoomMemberParams,
                   "Remove an agent from a room. Its open tasks there are released; if it was the "
                   "room's representative, the next agent takes over."),
        ActionSpec("create_task", "allowed", CreateTaskParams,
                   "Create a task in a room, optionally assigned to a participant."),
        ActionSpec("reassign_task", "allowed", ReassignTaskParams, "Give a task to another participant."),
        ActionSpec("pause_routine", "allowed", RoutineRefParams, "Pause a routine."),
        ActionSpec("resume_routine", "allowed", RoutineRefParams, "Resume a paused routine."),
        ActionSpec("pause_goal", "allowed", GoalRefParams, "Pause a goal."),
        ActionSpec("resume_goal", "allowed", GoalRefParams, "Resume a paused goal."),
        ActionSpec("create_routine", "ask", CreateRoutineParams,
                   "Create a routine: recurring work for an agent. trigger_config is {cron: '0 9 * * *'} "
                   "or {interval_seconds: 3600}; the agent must be in report_room_id."),
        ActionSpec("create_goal", "ask", CreateGoalParams,
                   "Start a goal in a project room with a lead: cycles that improve a measured score. spec is "
                   "{title, purpose, mode: target|optimize, metric: {name, direction: maximize|minimize, "
                   "target}, evaluator: {command}, deadline_at}; budget: {max_total_tokens} only when the "
                   "person asked for a token limit."),
        ActionSpec("archive_room", "ask", ArchiveRoomParams, "Archive a room and its sub-rooms."),
        ActionSpec("hire_agent", "ask", HireAgentParams,
                   "Propose a new agent. Needs can_create_agents; an admin approves.", admin_only=True),
    )
}

SELF_DIRECTED_POLICY: dict[str, Tier] = {
    # #930 decision 6 — recorded here so the policy reads in one place.
    "goal_retrospective_updates_instructions": "allowed",
}


def permission_rank(level: str | None) -> int:
    return PERMISSION_LEVELS.index(level or "standard")


def capped_permission(requested: str | None, creator: str | None) -> str | None:
    """The new agent's tier: never above the agent that proposed it (#938)."""

    if requested is None:
        return None if permission_rank(creator) >= permission_rank(None) else creator
    return requested if permission_rank(requested) <= permission_rank(creator) else (creator or "standard")
