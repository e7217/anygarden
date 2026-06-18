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

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Participant, Room, Task, TaskBlocker
from anygarden.skills_library.service import (
    SkillLibraryService,
    SkillNameConflictError,
    SkillNotFoundError,
    SkillOwnershipError,
)

log = logging.getLogger(__name__)

# Terminal task statuses — a blocker is "satisfied" once it reaches one of
# these. Reused by the #459 resolve-wake hook and the cycle/blocker walks.
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed"})

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
    {
        "name": "add_task_blocker",
        "description": (
            "Record that one of your tasks is blocked by another task — "
            "it must wait until the blocker reaches a terminal status "
            "(done/failed) before it can proceed. Only the agent that "
            "owns the dependent task's assignee participant may call "
            "this. When the last blocker finishes, the dependent task is "
            "automatically returned to 'todo' and you are re-notified. "
            "Self-references and dependency cycles are rejected."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "The dependent task (yours) that is waiting."
                    ),
                },
                "blocked_by_task_id": {
                    "type": "string",
                    "description": (
                        "The prerequisite task that must finish first."
                    ),
                },
            },
            "required": ["task_id", "blocked_by_task_id"],
        },
    },
    {
        "name": "clear_task_blocker",
        "description": (
            "Remove a previously recorded blocker edge between two of "
            "your tasks. Only the agent that owns the dependent task's "
            "assignee participant may call this. Use it when a "
            "dependency no longer applies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The dependent task (yours).",
                },
                "blocked_by_task_id": {
                    "type": "string",
                    "description": "The prerequisite task to unlink.",
                },
            },
            "required": ["task_id", "blocked_by_task_id"],
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
    elif status in TERMINAL_STATUSES and task.finished_at is None:
        task.finished_at = now
    await db.flush()

    # #459 (Wave 2c) — resolve-wake. When this task reaches a terminal
    # status, any task that was blocked *by* it may now be unblocked. The
    # hook clears the satisfied edge and re-wakes dependents whose blockers
    # are *all* terminal. Mirrors the REST path in api/v1/tasks.update_task.
    woken: list[str] = []
    if status in TERMINAL_STATUSES:
        woken = await resolve_task_blockers(db, completed_task_id=task.id)

    return _ok_result(
        f"task {task_id} status -> {status}",
        structured={"task_id": task_id, "status": status, "woken": woken},
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


# ── task_blockers (#459, Wave 2c) ───────────────────────────────────


async def _resolve_assigned_task(
    db: AsyncSession, *, agent_id: str, task_id: str
) -> tuple[Task | None, dict[str, Any] | None]:
    """Look up *task_id* and confirm *agent_id* owns its assignee.

    Returns ``(task, None)`` on success, or ``(None, error_result)`` shaped
    like :func:`_error_result`. Mirrors ``mark_task_status``'s assignee-only
    authorization so an agent can only manage blocker edges on tasks it is
    actually responsible for.
    """
    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        return None, _error_result(f"task not found: {task_id}")
    if not task.assignee_participant_id:
        return None, _error_result(
            "forbidden: task has no assignee — it cannot be managed by anyone"
        )
    assignee = (
        await db.execute(
            select(Participant).where(
                Participant.id == task.assignee_participant_id
            )
        )
    ).scalar_one_or_none()
    if assignee is None or assignee.agent_id != agent_id:
        return None, _error_result(
            "forbidden: only the assignee agent may manage this task's blockers"
        )
    return task, None


async def _is_transitively_blocked_by(
    db: AsyncSession, *, root: str, candidate: str
) -> bool:
    """Return True iff *root* is (transitively) blocked by *candidate*.

    Walks the ``task_blockers`` graph from *root* over its
    ``blocked_by_task_id`` edges (BFS), bounded by a visited set so a
    pre-existing cycle in the data cannot loop forever. Used by
    ``add_task_blocker`` to reject an edge ``task_id -> blocked_by`` when
    ``blocked_by`` already depends on ``task_id`` — which would close a
    cycle and leave both tasks blocked forever.
    """
    visited: set[str] = set()
    frontier: list[str] = [root]
    while frontier:
        current = frontier.pop()
        if current in visited:
            continue
        visited.add(current)
        rows = (
            await db.execute(
                select(TaskBlocker.blocked_by_task_id).where(
                    TaskBlocker.task_id == current
                )
            )
        ).scalars().all()
        for nxt in rows:
            if nxt == candidate:
                return True
            if nxt not in visited:
                frontier.append(nxt)
    return False


async def add_task_blocker(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Record that ``task_id`` is blocked by ``blocked_by_task_id``.

    Authorization: assignee-only on ``task_id`` (the dependent), same as
    ``mark_task_status``. Rejects self-reference and any edge that would
    close a dependency cycle (transitive guard). The insert is idempotent —
    re-adding an existing edge succeeds without error.
    """
    task_id = arguments.get("task_id")
    blocked_by = arguments.get("blocked_by_task_id")
    if not task_id:
        return _error_result("missing required argument: task_id")
    if not blocked_by:
        return _error_result("missing required argument: blocked_by_task_id")
    if task_id == blocked_by:
        return _error_result(
            "a task cannot block itself (task_id == blocked_by_task_id)"
        )

    task, err = await _resolve_assigned_task(
        db, agent_id=agent_id, task_id=task_id
    )
    if err is not None:
        return err

    blocker = (
        await db.execute(select(Task).where(Task.id == blocked_by))
    ).scalar_one_or_none()
    if blocker is None:
        return _error_result(f"blocker task not found: {blocked_by}")

    # Cycle guard: if the prospective blocker already (transitively)
    # depends on this task, adding ``task_id -> blocked_by`` would close a
    # cycle (A→B→A) and neither could ever clear. Reject at add time.
    if await _is_transitively_blocked_by(db, root=blocked_by, candidate=task_id):
        return _error_result(
            "rejected: this edge would create a dependency cycle "
            f"({task_id} <-> {blocked_by})"
        )

    # Idempotent insert — the composite PK makes a duplicate a no-op.
    existing = (
        await db.execute(
            select(TaskBlocker).where(
                TaskBlocker.task_id == task_id,
                TaskBlocker.blocked_by_task_id == blocked_by,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(TaskBlocker(task_id=task_id, blocked_by_task_id=blocked_by))
        await db.flush()

    return _ok_result(
        f"task {task_id} now blocked by {blocked_by}",
        structured={"task_id": task_id, "blocked_by_task_id": blocked_by},
    )


async def clear_task_blocker(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Delete the ``task_id`` -> ``blocked_by_task_id`` blocker edge.

    Authorization: assignee-only on ``task_id`` (the dependent). Removing a
    non-existent edge is a no-op success (idempotent).
    """
    task_id = arguments.get("task_id")
    blocked_by = arguments.get("blocked_by_task_id")
    if not task_id:
        return _error_result("missing required argument: task_id")
    if not blocked_by:
        return _error_result("missing required argument: blocked_by_task_id")

    _task, err = await _resolve_assigned_task(
        db, agent_id=agent_id, task_id=task_id
    )
    if err is not None:
        return err

    result = await db.execute(
        delete(TaskBlocker).where(
            TaskBlocker.task_id == task_id,
            TaskBlocker.blocked_by_task_id == blocked_by,
        )
    )
    await db.flush()
    return _ok_result(
        f"blocker {blocked_by} cleared from task {task_id}",
        structured={
            "task_id": task_id,
            "blocked_by_task_id": blocked_by,
            "removed": bool(result.rowcount),
        },
    )


async def resolve_task_blockers(
    db: AsyncSession,
    *,
    completed_task_id: str,
) -> list[str]:
    """Resolve-wake hook for a task that just reached a terminal status.

    Called from BOTH terminal paths (``mark_task_status`` here and
    ``api/v1/tasks.update_task``) after the status write + flush. For each
    dependent that was blocked by ``completed_task_id``:

    1. delete the now-satisfied ``(dependent, completed)`` edge;
    2. check the dependent's *remaining* blockers — if every one of them is
       terminal (done/failed), the dependent is fully unblocked;
    3. only then (and only if the dependent is currently in a waiting state —
       ``blocked``/``todo``/``failed``) return it to ``todo``, refresh
       ``assigned_at``, and re-inject its assignment mention so the assignee
       agent wakes through the existing mention path.

    "Wake only when ALL blockers are cleared" (plan §3.2): waking on a
    partial release would re-activate a task still stuck behind other
    prerequisites. Returns the list of woken dependent task ids (for tests
    and observability). Bounded + resilient — a single dependent failing to
    wake is logged and does not abort the rest.
    """
    # Reverse lookup — every dependent that names this task as a blocker.
    dependent_ids = (
        await db.execute(
            select(TaskBlocker.task_id).where(
                TaskBlocker.blocked_by_task_id == completed_task_id
            )
        )
    ).scalars().all()

    if not dependent_ids:
        return []

    log.info(
        "resolve_task_blockers: task %s terminal — %d dependent(s) to check",
        completed_task_id,
        len(dependent_ids),
    )

    # Lazy import — avoids a top-level cycle between mcp/tools and
    # messages/service (the latter imports anygarden.db.models).
    from anygarden.messages.service import inject_task_assignment_message

    woken: list[str] = []
    for dep_id in dependent_ids:
        try:
            # 1. Drop the satisfied edge.
            await db.execute(
                delete(TaskBlocker).where(
                    TaskBlocker.task_id == dep_id,
                    TaskBlocker.blocked_by_task_id == completed_task_id,
                )
            )
            await db.flush()

            # 2. Any remaining blocker still pending? Join the dependent's
            # remaining blocker edges to their blocker tasks' status.
            remaining = (
                await db.execute(
                    select(Task.status)
                    .join(
                        TaskBlocker,
                        TaskBlocker.blocked_by_task_id == Task.id,
                    )
                    .where(TaskBlocker.task_id == dep_id)
                )
            ).scalars().all()
            if any(s not in TERMINAL_STATUSES for s in remaining):
                # Still blocked by something unfinished — do not wake.
                continue

            # 3. Fully unblocked. Wake the dependent if it is in a waiting
            # state and still has an agent assignee to notify.
            dep = (
                await db.execute(select(Task).where(Task.id == dep_id))
            ).scalar_one_or_none()
            if dep is None:
                continue
            if dep.status not in ("blocked", "todo", "failed"):
                # Already moving (in_progress) or done — leave it alone.
                continue
            if not dep.assignee_participant_id:
                # No assignee to wake; just normalize the status.
                dep.status = "todo"
                await db.flush()
                continue

            assignee = (
                await db.execute(
                    select(Participant).where(
                        Participant.id == dep.assignee_participant_id
                    )
                )
            ).scalar_one_or_none()

            dep.status = "todo"
            dep.assigned_at = datetime.now(timezone.utc)
            await db.flush()

            # Human assignees don't auto-execute (mirrors api/v1/tasks
            # ``_maybe_inject``): only re-wake agents.
            if assignee is not None and assignee.agent_id is not None:
                room = (
                    await db.execute(
                        select(Room).where(Room.id == dep.room_id)
                    )
                ).scalar_one_or_none()
                if room is not None:
                    await inject_task_assignment_message(
                        db,
                        room=room,
                        task=dep,
                        sender_participant_id=dep.assignee_participant_id,
                        event="reassigned",
                    )
            woken.append(dep_id)
        except Exception:  # pragma: no cover — defence in depth
            log.exception(
                "resolve_task_blockers: failed to process dependent %s "
                "(blocker %s); continuing",
                dep_id,
                completed_task_id,
            )

    if woken:
        log.info(
            "resolve_task_blockers: woke %d dependent(s) after %s: %s",
            len(woken),
            completed_task_id,
            woken,
        )
    return woken
