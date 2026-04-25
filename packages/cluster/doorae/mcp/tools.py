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

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Participant, Task
from doorae.skills_library.service import (
    SkillLibraryService,
    SkillNameConflictError,
    SkillNotFoundError,
    SkillOwnershipError,
)

# Allowed task status values for ``mark_task_status`` (#266). Mirrored
# in the JSON Schema below so the LLM gets a clear enum hint.
TASK_STATUS_VALUES: tuple[str, ...] = ("todo", "in_progress", "done", "blocked")

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

    task.status = status
    await db.flush()

    return _ok_result(
        f"task {task_id} status -> {status}",
        structured={"task_id": task_id, "status": status},
    )
