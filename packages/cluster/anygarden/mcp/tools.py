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
import os
from hashlib import sha256
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Participant, Room, Task, TaskBlocker
from anygarden.rooms.authorization import Capability, require_capability
from anygarden.skills_library.service import (
    SkillLibraryService,
    SkillNameConflictError,
    SkillNotFoundError,
    SkillOwnershipError,
)

# #471 — the canonical status vocabulary now lives in a tiny, import-light
# module so the REST schemas (api/v1/tasks.py) can reuse it without pulling
# the MCP router into their import graph. Re-exported here so the historical
# ``from anygarden.mcp.tools import TASK_STATUS_VALUES`` path keeps working.
from anygarden.tasks_status import TASK_STATUS_VALUES, TERMINAL_STATUSES

log = logging.getLogger(__name__)

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
        "name": "claim_task",
        "description": (
            "Atomically claim an unassigned todo task for your current room "
            "participant. If another participant wins first, the call returns "
            "TASK_CLAIM_CONFLICT and does not overwrite their claim."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "mark_task_status",
        "description": (
            "Update the status of a task currently assigned to you. "
            "Only the agent that owns the task's assignee participant "
            "may call this. Use this when you finish a unit of work, "
            "begin one, or hit a blocker so the room (and the task's "
            "human stakeholders) stay in sync. A completed independent QA review "
            "uses status=done with verification verdict=pass or fail and the exact "
            "bound target task/version. Describe fixable findings in the failed review "
            "result; use blocked for unavailable required inputs or execution capability."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": list(TASK_STATUS_VALUES),
                },
                "result_markdown": {
                    "type": "string",
                    "description": "Result and verification evidence, passed to dependent tasks.",
                },
                "error": {
                    "type": "string",
                    "description": "Actionable reason for a failed or blocked task.",
                },
                "artifacts": {
                    "type": "array", "items": {"type": "object"},
                    "description": "Optional selection of actual publications. Omit or "
                                   "leave empty to connect this invocation's published files "
                                   "automatically. Each supplied reference needs artifact_id "
                                   "only; supplied metadata must match the server exactly.",
                },
                "verification": {
                    "type": "object",
                    "description": "Actual verification evidence; QA includes verdict and exact target version.",
                },
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "Create a new task in a room you orchestrate. Use the current "
            "room ID from your room context for room_id, never your agent "
            "ID or a participant ID. Optionally "
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
                "spec": {
                    "type": "string",
                    "maxLength": 100000,
                    "description": "Objective, supplied inputs, constraints, expected output and completion criteria.",
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
        "name": "ask_peer",
        "description": (
            "Ask other agents in this room to answer questions. Use this "
            "instead of writing a routing token in your reply. Put every peer "
            "you need in one call. The questions are posted at once in a "
            "thread under the message you are answering and the peers start "
            "working. Your reply for this turn is then kept as a draft, not "
            "posted: note what you already found yourself and end your turn. "
            "When all peers have finished you are woken with their answers "
            "and write the final answer once. A peer that is answering "
            "something in this room right now is rejected; one that already "
            "finished can be asked again. Only agents can be asked; address "
            "people by name in your reply."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "room_id": {
                    "type": "string",
                    "description": "The current room ID from your roster.",
                },
                "asks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "description": "One entry per peer.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "participant_id": {
                                "type": "string",
                                "description": "The peer agent's id from your roster.",
                            },
                            "question": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 2000,
                                "description": (
                                    "What you want this peer to answer. Include "
                                    "the context it needs; it does not see your "
                                    "conversation."
                                ),
                            },
                        },
                        "required": ["participant_id", "question"],
                    },
                },
            },
            "required": ["room_id", "asks"],
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


async def claim_task(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Atomically claim an open task as the calling agent participant."""

    from anygarden.task_service import TaskMutationConflict, claim_task_cas

    task_id = arguments.get("task_id")
    if not task_id:
        return _error_result("missing required argument: task_id")
    task = await db.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        return _error_result(f"task not found: {task_id}")
    try:
        access = await require_capability(
            db,
            room_id=task.room_id,
            identity=Identity(kind="agent", id=agent_id),
            capability=Capability.TASK_CLAIM,
            task=task,
        )
        if access.participant is None:
            return _error_result("forbidden: room participant required")
        claimed = await claim_task_cas(
            db,
            task_id=task.id,
            room_id=task.room_id,
            participant_id=access.participant.id,
        )
    except HTTPException as exc:
        return _error_result(f"forbidden: {exc.detail}")
    except TaskMutationConflict as exc:
        return _error_result(
            f"{exc.code}: {exc.detail}; current_status={exc.current_status!r}; "
            f"current_assignee_participant_id="
            f"{exc.current_assignee_participant_id!r}"
        )
    return _ok_result(
        f"task {task_id} claimed",
        structured={
            "task_id": task_id,
            "room_id": claimed.room_id,
            "status": claimed.status,
            "event": "claimed",
        },
    )


async def mark_task_status(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
    proof: Any = None,
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

    for field in ("result_markdown", "error"):
        if field in arguments and not isinstance(arguments[field], str):
            return _error_result(f"{field} must be a string")

    task = (
        await db.execute(select(Task).where(Task.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        return _error_result(f"task not found: {task_id}")
    if task.execution_id is not None:
        from anygarden.project_executions import service as execution_service

        try:
            if proof is None:
                return _error_result("Execution tasks require the current delivered turn lease")
            turn = await execution_service.authorize_turn(db, agent_id=agent_id, proof=proof)
            if turn.task_id != task.id:
                return _error_result("Execution task does not belong to this turn")
            execution = await db.get(execution_service.ProjectExecution, task.execution_id)
            if execution is None or execution.input_revision != task.input_revision:
                return _error_result("Execution input revision is no longer current")
            if execution.status in {"completed", "cancelled", "failed", "limit_reached"}:
                return _error_result("Execution is no longer accepting task updates")
            if task.id == execution.root_task_id and status == "done":
                return _error_result("Use complete_project_execution to check required work and publish the final report")
            if status == "done" and task.status != "done":
                await execution_service.finalize_task_result(
                    db, task=task, agent_id=agent_id, proof=proof,
                    result_markdown=arguments.get("result_markdown", ""),
                    artifacts=arguments.get("artifacts"),
                    verification=arguments.get("verification"),
                )
        except (HTTPException, ValueError) as exc:
            return _error_result(str(exc.detail) if isinstance(exc, HTTPException) else str(exc))
    from anygarden.task_service import (
        TaskMutationConflict,
        claim_task_cas,
        transition_task_status_cas,
    )

    try:
        if status == "in_progress":
            access = await require_capability(
                db,
                room_id=task.room_id,
                identity=Identity(kind="agent", id=agent_id),
                capability=Capability.TASK_CLAIM,
                task=task,
            )
            if access.participant is None:
                return _error_result("forbidden: room participant required")
            if (
                task.status == "in_progress"
                and task.assignee_participant_id == access.participant.id
            ):
                return _ok_result(
                    f"task {task_id} already in_progress",
                    structured={
                        "task_id": task_id,
                        "status": status,
                        "woken": [],
                        "event": "updated",
                    },
                )
            if task.assignee_participant_id != access.participant.id:
                return _error_result(
                    "TASK_CLAIM_CONFLICT: mark_task_status(in_progress) only "
                    "accepts a task already reserved for this agent; use "
                    "claim_task for an unassigned task"
                )
            task = await claim_task_cas(
                db,
                task_id=task.id,
                room_id=task.room_id,
                participant_id=access.participant.id,
            )
        else:
            access = await require_capability(
                db,
                room_id=task.room_id,
                identity=Identity(kind="agent", id=agent_id),
                capability=Capability.TASK_UPDATE,
                task=task,
                changed_fields={"status"},
            )
            if status == task.status:
                return _ok_result(
                    f"task {task_id} already {status}",
                    structured={"task_id": task_id, "status": status, "woken": []},
                )
            if task.status != "in_progress" or status not in {
                "blocked",
                "done",
                "failed",
            }:
                return _error_result(
                    f"TASK_INVALID_TRANSITION: cannot transition "
                    f"{task.status} to {status}"
                )
            assert access.participant is not None
            task = await transition_task_status_cas(
                db,
                task=task,
                target_status=status,
                participant_id=access.participant.id,
            )
    except HTTPException as exc:
        return _error_result(f"forbidden: {exc.detail}")
    except TaskMutationConflict as exc:
        return _error_result(f"{exc.code}: {exc.detail}")

    if "result_markdown" in arguments:
        task.result_markdown = arguments["result_markdown"]
    if "error" in arguments:
        task.error = arguments["error"]
    await db.flush()

    # #459 (Wave 2c) — resolve-wake. When this task reaches a terminal
    # status, any task that was blocked *by* it may now be unblocked. The
    # hook clears the satisfied edge and re-wakes dependents whose blockers
    # are *all* terminal. Mirrors the REST path in api/v1/tasks.update_task.
    woken: list[str] = []
    if status in TERMINAL_STATUSES:
        woken = await resolve_task_blockers(db, completed_task_id=task.id)
        if task.execution_id is not None:
            from anygarden.project_executions.service import reconcile_execution

            await reconcile_execution(db, task=task)

    deleted = False
    if task.goal_id is not None and status in TERMINAL_STATUSES:
        from anygarden.goals.executor import apply_completion

        deleted = await apply_completion(db, task, final_status=status)

    return _ok_result(
        f"task {task_id} status -> {status}",
        structured={
            "task_id": task_id,
            "status": status,
            "woken": woken,
            "event": "deleted" if deleted else (
                "claimed" if status == "in_progress" else "updated"
            ),
            "deleted": deleted,
        },
    )


# ── create_task (#270) ─────────────────────────────────────────────

# Issue #484 — "open" (still-actionable) statuses for the orchestrator
# create_task safeguards. Both the soft in-flight dedup probe and the
# fan-out cap count only these; a ``done``/``failed`` task neither blocks
# a legit repeat of the same title nor consumes a cap slot. ``blocked`` is
# deliberately excluded so a wedged blocker graph cannot mask the cap or
# false-dedup against a task no one is actively working — mirrors the
# ``status IN ('todo','in_progress')`` probe the goal path (#449) uses and
# is covered by the ``ix_tasks_room_status`` index.
_OPEN_TASK_STATUSES: tuple[str, ...] = ("todo", "in_progress")

# Default ceiling on the number of *open* tasks a single room may hold.
# Generous enough for normal decomposition (10–20 tasks) while still
# capping a runaway orchestrator loop. Tunable per-deployment via the
# ``ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM`` env var (read at call time so a
# test / operator can override without restarting).
_DEFAULT_MAX_OPEN_TASKS_PER_ROOM = 50


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
    try:
        access = await require_capability(
            db,
            room_id=room_id,
            identity=Identity(kind="agent", id=agent_id),
            capability=Capability.TASK_CREATE,
        )
    except HTTPException as exc:
        return _error_result(f"forbidden: {exc.detail}")
    room = access.room
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

    clean_title = title.strip()
    spec = arguments.get("spec")
    if spec is not None and (not isinstance(spec, str) or len(spec) > 100000):
        return _error_result("spec must be a string of at most 100000 characters")

    # ── Soft in-flight dedup (#484) ──────────────────────────────
    # An orchestrator LLM that calls create_task twice in one turn (or
    # re-fires after a transient error) would otherwise spawn duplicate
    # tasks — the self-loop guard only stops *self*-assignment. Before we
    # persist, look for an already-open task with the same
    # (room, assignee, title). On a hit we return that task's id with
    # ``deduplicated=True`` and do NOT re-inject the assignment mention
    # (the assignee already woke on the first create) — a fail-soft,
    # idempotent success rather than a second row. The probe matches a
    # NULL assignee consistently via ``IS NULL`` so unassigned repeats
    # collapse too. This is a *soft* guard (a true exactly-once token is
    # the follow-up #449-style ``idempotency_key`` work); a near-
    # simultaneous race could still slip two rows through, but the
    # per-room single-turn lock serialises the orchestrator in practice.
    existing_id = (
        await db.execute(
            select(Task.id)
            .where(
                Task.room_id == room_id,
                Task.assignee_participant_id == assignee_pid
                if assignee_pid is not None
                else Task.assignee_participant_id.is_(None),
                Task.title == clean_title,
                Task.spec == spec if spec is not None else Task.spec.is_(None),
                Task.status.in_(_OPEN_TASK_STATUSES),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        return _ok_result(
            f"task {existing_id!r} already open (deduplicated)",
            structured={
                "task_id": existing_id,
                "room_id": room_id,
                "assignee_pid": assignee_pid,
                "status": status,
                "deduplicated": True,
            },
        )

    # ── Fan-out cap (#484) ───────────────────────────────────────
    # No dedup hit means we're about to create a genuinely new task.
    # Guard against a runaway loop by refusing once the room already
    # holds ``ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM`` open tasks. This is a
    # fail-soft tool error (``isError: true``) — the orchestrator reads
    # it and stops rather than crashing the turn. A malformed env value
    # falls back to the default so a typo can't disable the safeguard.
    try:
        cap = int(
            os.environ.get(
                "ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM",
                _DEFAULT_MAX_OPEN_TASKS_PER_ROOM,
            )
        )
    except (TypeError, ValueError):
        cap = _DEFAULT_MAX_OPEN_TASKS_PER_ROOM
    open_count = (
        await db.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.room_id == room_id,
                Task.status.in_(_OPEN_TASK_STATUSES),
            )
        )
    ).scalar_one()
    if open_count >= cap:
        return _error_result(
            f"open task cap reached for this room ({open_count}/{cap}); "
            "refusing to create another task. Close or complete some open "
            "tasks first, or raise ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM."
        )

    # ── Persist + inject ─────────────────────────────────────────
    task = Task(
        room_id=room_id,
        title=clean_title,
        status=status,
        assignee_participant_id=assignee_pid,
        # ``created_by`` is for User authors — agent-created tasks
        # leave it NULL. The synthetic message metadata carries the
        # full provenance.
        created_by=None,
        spec=spec,
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

    # Prerequisites may cross sub-rooms within one project, never projects.
    # A non-project room is isolated to itself.
    own_room = await db.get(Room, task.room_id)
    blocker_room = await db.get(Room, blocker.room_id)
    if (
        own_room is None or blocker_room is None
        or (own_room.id != blocker_room.id and (
            own_room.project_id is None
            or own_room.project_id != blocker_room.project_id
        ))
    ):
        return _error_result("forbidden: prerequisite must belong to the same project")
    try:
        await require_capability(
            db, room_id=blocker.room_id,
            identity=Identity(kind="agent", id=agent_id),
            capability=Capability.TASK_READ,
        )
    except HTTPException as exc:
        return _error_result(f"forbidden: {exc.detail}")

    if task.status not in {"todo", "in_progress", "blocked"}:
        return _error_result("cannot add a prerequisite to a terminal task")

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

    # An already successful prerequisite must immediately supply its input;
    # otherwise this edge would wait forever for an event that already fired.
    if blocker.status in TERMINAL_STATUSES:
        await resolve_task_blockers(db, completed_task_id=blocker.id)

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
    """Capture successful inputs and wake only fully satisfied dependents.

    Failed prerequisites retain their edge and an actionable waiting reason.
    Successful result snapshots survive edge removal and retries, and are
    supplied to the assignee in the resumed assignment message.
    """
    completed = await db.get(Task, completed_task_id)
    if completed is None or completed.status not in TERMINAL_STATUSES:
        return []
    qa_failed = False
    if completed.execution_id and completed.role == "qa" and completed.status == "done":
        from anygarden.db.models import TaskResult

        qa_result = await db.scalar(select(TaskResult).where(
            TaskResult.task_id == completed.id,
            TaskResult.execution_id == completed.execution_id,
            TaskResult.input_revision == completed.input_revision,
            TaskResult.version == completed.result_version,
        ))
        qa_failed = qa_result is None or (qa_result.verification or {}).get("verdict") != "pass"
        if not qa_failed:
            from anygarden.project_executions.qa_repairs import (
                rewire_satisfied_qa_edges,
            )

            await rewire_satisfied_qa_edges(db, execution_id=completed.execution_id)
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
    from anygarden.task_service import (
        TaskMutationConflict,
        transition_task_status_cas,
    )

    woken: list[str] = []
    for dep_id in dependent_ids:
        savepoint = None
        try:
            # Removing the last edge, freezing its result and dispatching the
            # dependent are one unit. A lost claim must retain the old edge.
            savepoint = await db.begin_nested()
            dep = await db.get(Task, dep_id)
            if dep is None or dep.status not in {"blocked", "todo", "in_progress"}:
                continue
            dep_room = await db.get(Room, dep.room_id)
            completed_room = await db.get(Room, completed.room_id)
            if (
                dep_room is None or completed_room is None
                or (dep_room.id != completed_room.id and (
                    dep_room.project_id is None
                    or dep_room.project_id != completed_room.project_id
                ))
            ):
                continue
            if completed.status == "failed" or qa_failed:
                if dep.status == "todo":
                    dep = await transition_task_status_cas(
                        db, task=dep, target_status="blocked",
                    )
                dep.error = ("DEPENDENCY_QA_NOT_PASSED" if qa_failed else
                             f"Prerequisite {completed.id} failed: "
                             f"{completed.error or 'retry or explicitly replace this prerequisite'}")
                await db.flush()
                continue

            result = completed.result_markdown
            snapshot = {
                "task_id": completed.id,
                "room_id": completed.room_id,
                "title": completed.title,
                "result_markdown": result,
                "result_sha256": sha256(result.encode()).hexdigest() if result is not None else None,
                "finished_at": completed.finished_at.isoformat() if completed.finished_at else None,
            }
            if completed.execution_id:
                from anygarden.db.models import TaskResult

                accepted = await db.scalar(select(TaskResult).where(
                    TaskResult.task_id == completed.id,
                    TaskResult.execution_id == completed.execution_id,
                    TaskResult.input_revision == completed.input_revision,
                    TaskResult.version == completed.result_version,
                ))
                if dep.execution_id != completed.execution_id or dep.input_revision != completed.input_revision:
                    continue
                if accepted is None:
                    dep.error = "Prerequisite has no accepted result for this execution input revision"
                    await db.flush()
                    continue
                snapshot.update({
                    "execution_id": completed.execution_id,
                    "input_revision": accepted.input_revision,
                    "result_id": accepted.id, "result_version": accepted.version,
                    "result_sha256": accepted.result_sha256,
                    "result_markdown": accepted.result_markdown,
                    "artifacts": accepted.artifacts, "verification": accepted.verification,
                    "result_created_at": accepted.created_at.isoformat(),
                })
            snapshots = list(dep.dependency_results or [])
            if snapshot not in snapshots:
                dep.dependency_results = [*snapshots, snapshot]
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
            if remaining:
                # Still blocked by something unfinished — do not wake.
                continue

            # 3. Fully unblocked. Wake the dependent if it is in a waiting
            # state and still has an agent assignee to notify.
            if dep.status not in ("blocked", "todo"):
                # Moving or terminal work must not be reopened implicitly.
                continue
            if dep.status == "blocked":
                try:
                    dep = await transition_task_status_cas(
                        db,
                        task=dep,
                        target_status="todo",
                    )
                except TaskMutationConflict:
                    await savepoint.rollback()
                    continue
            if not dep.assignee_participant_id:
                # No assignee to wake; status was normalized above.
                continue

            assignee = (
                await db.execute(
                    select(Participant).where(
                        Participant.id == dep.assignee_participant_id
                    )
                )
            ).scalar_one_or_none()

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
                    if dep.execution_id:
                        from anygarden.task_service import claim_task_cas

                        dep = await claim_task_cas(
                            db, task_id=dep.id, room_id=dep.room_id,
                            participant_id=dep.assignee_participant_id,
                        )
                    await inject_task_assignment_message(
                        db,
                        room=room,
                        task=dep,
                        sender_participant_id=dep.assignee_participant_id,
                        event="reassigned",
                    )
            woken.append(dep_id)
        except Exception:  # pragma: no cover — defence in depth
            if savepoint is not None and savepoint.is_active:
                await savepoint.rollback()
            log.exception(
                "resolve_task_blockers: failed to process dependent %s "
                "(blocker %s); continuing",
                dep_id,
                completed_task_id,
            )
        finally:
            if savepoint is not None and savepoint.is_active:
                await savepoint.commit()

    if woken:
        log.info(
            "resolve_task_blockers: woke %d dependent(s) after %s: %s",
            len(woken),
            completed_task_id,
            woken,
        )
    return woken


async def ask_peer(
    db: AsyncSession,
    *,
    agent_id: str,
    arguments: dict[str, Any],
    budget: Any,
) -> tuple[dict[str, Any], list[Any]]:
    """Ask peers now and collect their answers asynchronously (#762).

    Accepted questions are posted in the caller's thread at once and each
    peer's turn is started; the caller's reply for this turn is kept as a
    draft, and the caller is woken with every answer once all peers finish
    (:mod:`anygarden.orchestration.peer_fanin`). A peer that was itself
    called by a question (hop 2) cannot ask on: its request is handed back
    to the caller with its answer.

    Returns the tool result and the posted questions; the caller of this
    function commits, then broadcasts them. A rejection is a normal result
    (``isError`` false) the model should act on in the same turn; only bad
    arguments are tool errors.
    """
    from anygarden.orchestration.peer_ask import (
        MAX_ASKS_PER_CALL,
        MAX_QUESTION_CHARS,
        REASON_LIMIT_REACHED,
        check_peer_ask,
        resolve_caller,
        since_turn_start,
    )
    from anygarden.orchestration.peer_fanin import (
        open_or_join_group,
        post_question,
        record_forwarded_request,
    )
    from anygarden.orchestration.rules import MAX_PEER_DEPTH

    room_id = arguments.get("room_id")
    if not isinstance(room_id, str) or not room_id:
        return _error_result("room_id is required"), []
    raw_asks = arguments.get("asks")
    if raw_asks is None and "participant_id" in arguments:
        raw_asks = [{
            "participant_id": arguments.get("participant_id"),
            "question": arguments.get("question"),
        }]
    if not isinstance(raw_asks, list) or not raw_asks:
        return _error_result("asks is required"), []
    if len(raw_asks) > MAX_ASKS_PER_CALL:
        return _error_result(f"at most {MAX_ASKS_PER_CALL} asks per call"), []
    asks: list[tuple[str, str]] = []
    for item in raw_asks:
        pid = item.get("participant_id") if isinstance(item, dict) else None
        question = item.get("question") if isinstance(item, dict) else None
        if not isinstance(pid, str) or not pid:
            return _error_result("each ask needs a participant_id"), []
        if not isinstance(question, str) or not question.strip():
            return _error_result("each ask needs a question"), []
        question = question.strip()
        if len(question) > MAX_QUESTION_CHARS:
            return _error_result(
                f"question is longer than {MAX_QUESTION_CHARS} characters"
            ), []
        # A repeated target keeps its last question.
        asks = [a for a in asks if a[0] != pid] + [(pid, question)]

    caller = await resolve_caller(db, agent_id=agent_id, room_id=room_id)
    if caller is None:
        return _error_result("You are not a participant of this room."), []
    if caller.turn is None:
        return _error_result(
            "ask_peer can only be used while you are answering a message."
        ), []

    # Hop 2: this turn was started by a peer's question. Hand the request
    # back to that caller instead of calling on (#762).
    if caller.hop > MAX_PEER_DEPTH:
        forwarded = [
            (pid, q)
            for pid, q in asks
            if await record_forwarded_request(
                db, request_id=caller.turn.request_id, participant_id=pid, question=q
            )
        ]
        if forwarded:
            names = ", ".join(caller.names.get(pid, pid) for pid, _ in forwarded)
            return _ok_result(
                "Not sent: you were asked by another agent, so you cannot ask "
                f"peers yourself. Your request to {names} goes back to the agent "
                "that asked you, together with your answer; it decides whether "
                "to ask. Answer the question you were asked with what you have.",
                {
                    "status": "forwarded",
                    "targets": [
                        {"participant_id": pid, "name": caller.names.get(pid)}
                        for pid, _ in forwarded
                    ],
                },
            ), []
        recent = await since_turn_start(db, caller)
        return _rejected_result(
            [
                {
                    "participant_id": pid,
                    "name": caller.names.get(pid),
                    "status": "rejected",
                    "reason": REASON_LIMIT_REACHED,
                }
                for pid, _ in asks
            ],
            recent,
        ), []

    results: list[dict[str, Any]] = []
    accepted: list[tuple[Any, str]] = []
    for pid, question in asks:
        decision = await check_peer_ask(db, caller=caller, target_pid=pid)
        entry: dict[str, Any] = {
            "participant_id": pid,
            "name": decision.target_name,
            "status": decision.status,
        }
        if decision.status == "invalid":
            entry["detail"] = decision.detail
        elif decision.status == "rejected":
            entry["reason"] = decision.reason
        elif budget is not None and not budget.consume(room_id, 1):
            entry["status"] = "rejected"
            entry["reason"] = REASON_LIMIT_REACHED
        else:
            accepted.append((decision.target, question))
        results.append(entry)

    if len(asks) == 1 and results[0]["status"] == "invalid":
        return _error_result(results[0]["detail"] or "invalid peer"), []

    posted = []
    if accepted:
        group = await open_or_join_group(
            db, caller_turn=caller.turn, caller_participant_id=caller.participant.id
        )
        for target, question in accepted:
            posted.append(await post_question(db, group=group, target=target, question=question))
        if budget is not None:
            budget.mark_peer_called(room_id, [t.id for t, _ in accepted])

    if not accepted:
        return _rejected_result(results, await since_turn_start(db, caller)), []

    sent = ", ".join(
        caller.names.get(t.id, t.id) for t, _ in accepted
    )
    text = (
        f"Sent to {sent}. Each answers in a thread under the message you are "
        "answering. Your reply for this turn will NOT be posted: write down "
        "only what you have already found yourself (it is kept as your draft), "
        "then end your turn. When every peer has finished you will be woken "
        "with their answers and your draft, and you write the final answer then."
    )
    not_sent = [r for r in results if r["status"] != "accepted"]
    if not_sent:
        text += "\nNot sent: " + "; ".join(
            f"{r.get('name') or r['participant_id']} ({r.get('reason') or r.get('detail')})"
            for r in not_sent
        )
    return _ok_result(text, {"status": "sent", "targets": results}), posted


def _rejected_result(
    results: list[dict[str, Any]], recent_messages: list[dict[str, Any]]
) -> dict[str, Any]:
    whys = []
    for r in results:
        name = r.get("name") or r["participant_id"]
        if r.get("reason") == "already_answering":
            whys.append(f"{name} is answering a message in this room right now.")
        elif r.get("reason") == "limit_reached":
            whys.append(f"{name}: the peer-call limit for this user turn is reached.")
        else:
            whys.append(f"{name}: {r.get('detail') or 'invalid'}")
    recent = "\n".join(
        f"- #{m['seq']} {m['speaker']}: {m['content']}" for m in recent_messages
    )
    text = (
        "Rejected: " + " ".join(whys) + " No peer will be called. Do not ask "
        "for this in your reply; answer yourself, using the messages below if "
        "they help."
        + (f"\nMessages posted since your turn started:\n{recent}" if recent else "")
    )
    return _ok_result(
        text,
        {
            "status": "rejected",
            "targets": results,
            "since_turn_start": recent_messages,
        },
    )
