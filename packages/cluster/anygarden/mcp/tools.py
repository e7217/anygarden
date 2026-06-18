"""MCP tool handlers and JSON Schemas for agent self-authored skills (#120).

The MCP spec separates two failure modes:

- **Protocol-level errors** (malformed params, unknown tool,
  transport issues) → JSON-RPC ``error`` with a numeric code.
- **Tool-level errors** (validation failed, ownership violation,
  skill not found) → a normal ``result`` object with
  ``isError: true`` and a ``content`` array the LLM can read.

We map :class:`SkillOwnershipError`, :class:`SkillNameConflictError`,
and :class:`SkillNotFoundError` onto the second mode so the calling
LLM can decide what to do (rename, give up, surface to the user) —
a JSON-RPC error would propagate as a hard transport failure and
the LLM couldn't read the message.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Participant, Room, Task
from anygarden.skills_library.service import (
    SkillLibraryService,
    SkillNameConflictError,
    SkillNotFoundError,
    SkillOwnershipError,
)

# Allowed task status values for ``mark_task_status`` (#266, #319).
# ``failed`` (#319) joined the legal set when the goals sweeper started
# stamping it on pickup/execution timeouts; before that the LLM-facing
# enum and the sweeper's stored value drifted, so an agent that wanted
# to mark its own task as failed got a 4xx from the MCP RPC.
TASK_STATUS_VALUES: tuple[str, ...] = (
    "todo",
    "in_progress",
    "done",
    "blocked",
    "failed",
)

# ── Tool schemas ────────────────────────────────────────────────

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "create_skill",
        "description": (
            "Create a new skill belonging to the calling agent. The "
            "skill is auto-attached to this agent on its next spawn. "
            "Admins can later 'promote' the skill to the shared "
            "library so other agents can use it too."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Skill name (directory under skills/). Must be "
                        "unique within this agent's scope."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Short human-readable description of the skill."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": (
                        "Body of SKILL.md — the primary skill document "
                        "the LLM will read at invocation time."
                    ),
                },
                "extra_files": {
                    "type": "object",
                    "description": (
                        "Optional map of relative_path -> body for "
                        "supporting files (scripts, references). Keys "
                        "must already start with skills/<name>/."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["name", "description", "body"],
        },
    },
    {
        "name": "update_skill",
        "description": (
            "Rewrite the body or extra_files of a skill you previously "
            "created. Only the author may call this on a given skill."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "body": {"type": "string"},
                "extra_files": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_my_skills",
        "description": (
            "Return every skill authored by the calling agent with "
            "id, name, description, and creation timestamp."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_my_skill",
        "description": (
            "Delete a skill you authored. Cascades to attachments."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "mark_task_status",
        "description": (
            "Update the status of a task currently assigned to you. "
            "Only the agent that owns the task's assignee participant "
            "may call this. Use this when you finish a unit of work, "
            "begin one, or hit a blocker so the room (and the task's "
            "human stakeholders) stay in sync."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": list(TASK_STATUS_VALUES),
                },
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "Create a new task in a room you orchestrate, optionally "
            "assigning it to one of the room's agent participants. Call "
            "this multiple times in a single turn to break a complex "
            "user request into independently delegated units of work. "
            "Only the agent designated as the room's orchestrator may "
            "use this tool, and only when the room runs the "
            "``orchestrator`` speaker strategy."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "room_id": {"type": "string"},
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 500,
                },
                "assignee_pid": {
                    "type": ["string", "null"],
                    "description": (
                        "Participant id of the assignee. Must be a "
                        "participant of ``room_id`` and must not be "
                        "your own orchestrator participant (no "
                        "self-loops). Omit to create an unassigned "
                        "task that you intend to delegate later."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": list(TASK_STATUS_VALUES),
                    "default": "todo",
                },
            },
            "required": ["room_id", "title"],
        },
    },
]


# ── Tool dispatch ───────────────────────────────────────────────


def _error_result(message: str) -> dict[str, Any]:
    """Shape a tool-level error in the MCP-standard envelope."""
    return {
        "isError": True,
        "content": [{"type": "text", "text": message}],
    }


def _ok_result(
    text: str, structured: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Shape a successful tool result.  The structured payload is the
    machine-readable surface for the LLM; the text is a human-readable
    summary."""
    out: dict[str, Any] = {
        "isError": False,
        "content": [{"type": "text", "text": text}],
    }
    if structured is not None:
        out["structuredContent"] = structured
    return out


async def call_tool(
    service: SkillLibraryService,
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch a single ``tools/call`` to its handler."""
    if tool_name == "create_skill":
        return await _create_skill(service, agent_id, arguments)
    if tool_name == "update_skill":
        return await _update_skill(service, agent_id, arguments)
    if tool_name == "list_my_skills":
        return await _list_my_skills(service, agent_id)
    if tool_name == "delete_my_skill":
        return await _delete_my_skill(service, agent_id, arguments)
    return _error_result(f"unknown tool: {tool_name}")


async def _create_skill(
    service: SkillLibraryService,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    try:
        name = arguments["name"]
        description = arguments["description"]
        body = arguments["body"]
    except KeyError as exc:
        return _error_result(f"missing required argument: {exc.args[0]}")
    extras = arguments.get("extra_files")
    if extras is not None and not isinstance(extras, dict):
        return _error_result("extra_files must be an object of path->body strings")

    try:
        entry = await service.create_from_agent(
            agent_id=agent_id,
            name=name,
            description=description,
            body=body,
            extra_files=extras,
        )
    except SkillNameConflictError as exc:
        return _error_result(f"duplicate skill name: {exc}")
    except Exception as exc:  # pragma: no cover — defence in depth
        return _error_result(f"create_skill failed: {exc}")

    return _ok_result(
        f"skill {entry.name!r} created (id={entry.id})",
        structured={"id": entry.id, "pinned_rev": None, "name": entry.name},
    )


async def _update_skill(
    service: SkillLibraryService,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    skill_id = arguments.get("id")
    if not skill_id:
        return _error_result("missing required argument: id")
    body = arguments.get("body")
    extras = arguments.get("extra_files")
    if extras is not None and not isinstance(extras, dict):
        return _error_result("extra_files must be an object of path->body strings")

    try:
        entry = await service.update_by_owner(
            agent_id=agent_id,
            skill_id=skill_id,
            body=body,
            extra_files=extras,
        )
    except SkillOwnershipError as exc:
        return _error_result(f"forbidden: {exc}")
    except SkillNotFoundError as exc:
        return _error_result(str(exc))
    except Exception as exc:  # pragma: no cover — defence in depth
        return _error_result(f"update_skill failed: {exc}")

    return _ok_result(
        f"skill {entry.name!r} updated",
        structured={"id": entry.id},
    )


async def _list_my_skills(
    service: SkillLibraryService,
    agent_id: str,
) -> dict[str, Any]:
    rows = await service.list_by_owner(agent_id=agent_id)
    skills = [
        {
            "id": r.id,
            "name": r.name,
            # First line of SKILL.md as a lightweight description proxy
            # — we don't persist a separate description column (see
            # ``create_from_agent`` comment).
            "description": r.skill_md.splitlines()[0] if r.skill_md else "",
            "created_at": (
                r.fetched_at.isoformat() if r.fetched_at is not None else None
            ),
        }
        for r in rows
    ]
    return _ok_result(
        f"{len(skills)} skill(s)",
        structured={"skills": skills},
    )


async def _delete_my_skill(
    service: SkillLibraryService,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    skill_id = arguments.get("id")
    if not skill_id:
        return _error_result("missing required argument: id")
    try:
        deleted = await service.delete_by_owner(
            agent_id=agent_id, skill_id=skill_id
        )
    except SkillOwnershipError as exc:
        return _error_result(f"forbidden: {exc}")
    except SkillNotFoundError as exc:
        return _error_result(str(exc))
    except Exception as exc:  # pragma: no cover
        return _error_result(f"delete_my_skill failed: {exc}")
    return _ok_result(
        f"skill {skill_id} deleted",
        structured={"deleted": bool(deleted)},
    )


# ── mark_task_status (#266) ────────────────────────────────────────


async def mark_task_status(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Flip a task's ``status`` on behalf of the calling agent.

    Authorization: the caller's ``agent_id`` must match the agent that
    owns the task's current assignee participant. This protects against
    one agent silently completing another agent's work — a quiet but
    real failure mode in multi-agent rooms.

    Lives outside the legacy ``call_tool`` dispatcher (which only takes
    a ``SkillLibraryService``) so the MCP router wires this branch
    directly with a fresh DB session — see ``mcp/router.py``.
    """
    task_id = arguments.get("task_id")
    status = arguments.get("status")
    if not task_id:
        return _error_result("missing required argument: task_id")
    if not status:
        return _error_result("missing required argument: status")
    if status not in TASK_STATUS_VALUES:
        return _error_result(
            f"invalid status {status!r}; expected one of "
            f"{sorted(TASK_STATUS_VALUES)}"
        )

    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        return _error_result(f"task not found: {task_id}")

    if not task.assignee_participant_id:
        return _error_result(
            "forbidden: task has no assignee — it cannot be marked by anyone"
        )

    assignee = (
        await db.execute(
            select(Participant).where(Participant.id == task.assignee_participant_id)
        )
    ).scalar_one_or_none()
    if assignee is None or assignee.agent_id != agent_id:
        return _error_result(
            "forbidden: only the assignee agent may mark this task"
        )

    now = datetime.now(timezone.utc)
    task.status = status
    # #445 — stamp lifecycle timestamps so the execution-timeout sweeper
    # (goals/sweeper.py) can detect wedged in_progress tasks. The is-None
    # guard preserves a started_at already set at goal-task creation
    # (goals/executor.py) and keeps the first transition authoritative.
    if status == "in_progress" and task.started_at is None:
        task.started_at = now
    elif status in ("done", "failed") and task.finished_at is None:
        task.finished_at = now
    await db.flush()

    return _ok_result(
        f"task {task_id} status -> {status}",
        structured={"task_id": task_id, "status": status},
    )


# ── create_task (#270) ─────────────────────────────────────────────


async def create_task(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Create a task in a room the calling agent orchestrates.

    Authorization (plan §3.1):
    - The room must run ``speaker_strategy='orchestrator'``.
    - ``Room.orchestrator_agent_id`` must equal the caller's
      ``agent_id``.
    - The optional ``assignee_pid`` must be a participant of the
      target room AND must not point at the orchestrator's own
      participant (self-loop guard, plan §6 R2).

    On success the helper persists the row, then reuses Phase 1's
    synthetic mention injection so the assignee agent wakes through
    its existing ``decide_policy`` mention path — no new wake-up
    protocol is introduced. Phase 1 also wires the WS fanout, which
    the router applies after this handler returns (we deliberately
    return ``task_id`` so the router can re-fetch and broadcast).
    """
    # ── Validate inputs ──────────────────────────────────────────
    room_id = arguments.get("room_id")
    title = arguments.get("title")
    assignee_pid = arguments.get("assignee_pid")
    status = arguments.get("status", "todo")
    if not room_id:
        return _error_result("missing required argument: room_id")
    if not title or not isinstance(title, str) or not title.strip():
        return _error_result("missing required argument: title")
    if status not in TASK_STATUS_VALUES:
        return _error_result(
            f"invalid status {status!r}; expected one of "
            f"{sorted(TASK_STATUS_VALUES)}"
        )

    # ── Authorization ────────────────────────────────────────────
    room = (
        await db.execute(select(Room).where(Room.id == room_id))
    ).scalar_one_or_none()
    if room is None:
        return _error_result(f"room not found: {room_id}")
    if room.speaker_strategy != "orchestrator":
        return _error_result(
            "forbidden: room speaker strategy is not 'orchestrator'; "
            "create_task is reserved for orchestrator-driven rooms"
        )
    if room.orchestrator_agent_id != agent_id:
        return _error_result(
            "forbidden: only the room's orchestrator may create tasks"
        )

    # ── Optional assignee validation ─────────────────────────────
    if assignee_pid is not None:
        assignee = (
            await db.execute(
                select(Participant).where(Participant.id == assignee_pid)
            )
        ).scalar_one_or_none()
        if assignee is None or assignee.room_id != room_id:
            return _error_result(
                "assignee_pid is not a participant of this room"
            )
        # Self-loop guard: if the orchestrator assigns the task to
        # itself, its own ``decide_policy`` would wake again on the
        # synthetic mention, potentially re-decomposing forever.
        if assignee.agent_id == agent_id:
            return _error_result(
                "self-assignment is not allowed: orchestrator cannot "
                "assign a task to its own participant"
            )

    # ── Persist + inject ─────────────────────────────────────────
    task = Task(
        room_id=room_id,
        title=title.strip(),
        status=status,
        assignee_participant_id=assignee_pid,
        # ``created_by`` is for User authors — agent-created tasks
        # leave it NULL. The synthetic message metadata carries the
        # full provenance.
        created_by=None,
    )
    db.add(task)
    await db.flush()

    if assignee_pid is not None:
        # Lazy import — avoids a top-level cycle between mcp/tools and
        # messages/service (the latter imports anygarden.db.models).
        from anygarden.messages.service import inject_task_assignment_message

        # Sender: the orchestrator's own participant. We resolve it
        # rather than requiring the caller to pass it because the
        # orchestrator already authenticated as the room's conductor —
        # any other sender choice would muddle the provenance.
        orc_p = (
            await db.execute(
                select(Participant).where(
                    Participant.room_id == room_id,
                    Participant.agent_id == agent_id,
                )
            )
        ).scalar_one_or_none()
        sender_pid = orc_p.id if orc_p is not None else None
        await inject_task_assignment_message(
            db,
            room=room,
            task=task,
            sender_participant_id=sender_pid,
            event="assigned",
        )

    return _ok_result(
        f"task {task.id!r} created" + (
            f" and assigned to {assignee_pid}" if assignee_pid else ""
        ),
        structured={
            "task_id": task.id,
            "room_id": task.room_id,
            "assignee_pid": task.assignee_participant_id,
            "status": task.status,
        },
    )
