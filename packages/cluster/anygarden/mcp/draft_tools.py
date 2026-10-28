"""``propose_goal`` / ``propose_routine``: an agent's draft for a person (#947, #948).

Listed for every agent, but each works only in the turn a person's
``/goal`` or ``/routine`` draft request started (turn proof). The draft is
checked as the create routes would check it; a passing draft becomes an
approval card in the room. Logic lives in ``anygarden.workspace.drafts``.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from anygarden.mcp.project_tools import _tool
from anygarden.mcp.workspace_tools import _error, _ok

_string = {"type": "string", "minLength": 1}

_DRAFT_TAIL = (
    " Works only in the turn started by that request. The server checks the draft: an error "
    "comes back to you to fix and call again; a valid draft becomes a card the person creates "
    "with one click (they can edit or reject it). Call it once, tell the person, end your turn."
)

DRAFT_TOOL_SCHEMAS = [
    _tool(
        "propose_goal",
        "Submit the goal (metric goal) draft a person asked for with /goal." + _DRAFT_TAIL,
        {
            "title": {**_string, "maxLength": 500},
            "purpose": {**_string, "description": "What the goal is for, in the person's words"},
            "mode": {"type": "string", "enum": ["target", "optimize"],
                     "description": "target: stop at metric.target. optimize: improve until deadline_at"},
            "metric": {
                "type": "object",
                "properties": {
                    "name": {**_string, "maxLength": 120},
                    "direction": {"type": "string", "enum": ["maximize", "minimize"]},
                    "target": {"type": "number", "description": "Required for mode=target; omit for optimize"},
                    "min_delta": {"type": "number", "minimum": 0},
                },
                "required": ["name", "direction"],
            },
            "evaluator": {
                "type": "object",
                "properties": {
                    "command": {**_string, "description": "Shell command run in your workspace. The "
                                "last stdout line is one JSON object with the score under score_key "
                                'and a boolean "valid", e.g. {"score": 22, "valid": true}'},
                    "cwd": _string,
                    "timeout_sec": {"type": "integer", "minimum": 1},
                    "pin_paths": {"type": "array", "items": _string},
                    "score_key": _string,
                },
                "required": ["command"],
            },
            "budget": {
                "type": "object",
                "properties": {"max_total_tokens": {"type": "integer", "minimum": 1}},
                "required": ["max_total_tokens"],
            },
            "deadline_at": {"type": "string", "format": "date-time",
                            "description": "ISO timestamp with timezone; required for mode=optimize"},
        },
        ["title", "purpose", "mode", "metric", "evaluator", "budget"],
    ),
    _tool(
        "propose_routine",
        "Submit the routine (recurring work on a schedule) draft a person asked for with /routine."
        + _DRAFT_TAIL,
        {
            "agent_id": {**_string, "description": "The agent that runs it (fixed when the person named one)"},
            "title": {**_string, "maxLength": 500},
            "spec": {**_string, "description": "The instructions the agent follows on every run"},
            "trigger_type": {"type": "string", "enum": ["cron", "interval", "manual"]},
            "trigger_config": {"type": "object", "description": "{cron: '0 9 * * *', timezone: 'Asia/Seoul'} "
                               "or {interval_seconds: 3600}; {} for manual"},
            "report_room_id": {**_string, "description": "Defaults to this room"},
            "materialize": {"type": "string", "enum": ["full", "interesting_only"]},
        },
        ["title", "spec", "trigger_type", "trigger_config"],
    ),
]
DRAFT_TOOL_NAMES = frozenset(tool["name"] for tool in DRAFT_TOOL_SCHEMAS)


async def call_draft_tool(request: Request, *, agent_id: str, name: str, arguments: dict) -> dict:
    from anygarden.mcp.project_tools import broadcast_project_messages, turn_proof
    from anygarden.rooms.authorization import room_authorization_session
    from anygarden.workspace import drafts
    from anygarden.workspace.actions import ActionFailed
    from anygarden.workspace.requester import RequesterRefused, requester_for_turn

    kind = "goal" if name == "propose_goal" else "routine"
    session_factory = request.app.state.session_factory
    try:
        proof = turn_proof(request)
    except HTTPException as exc:
        return _error("TURN_PROOF_REQUIRED", str(exc.detail))
    try:
        async with session_factory() as db:
            requester = await requester_for_turn(db, agent_id=agent_id, proof=proof)
            await db.rollback()
    except RequesterRefused as exc:
        if exc.code == "NO_PERSON_TO_ACT_FOR":
            return _error("NOT_A_DRAFT_TURN", f"{name} works only in the turn started by a person's draft request")
        return _error(exc.code, exc.detail)
    try:
        async with room_authorization_session(session_factory) as db:
            row, message = await drafts.propose(
                db, requester=requester, agent_id=agent_id, kind=kind, arguments=arguments,
            )
            request_id = row.id
            await db.commit()
            if message is not None:
                await broadcast_project_messages(db, request=request, messages=[message])
    except ActionFailed as exc:
        return _error(exc.code, exc.detail)
    return _ok({
        "status": "pending_approval",
        "request_id": request_id,
        "next": "Tell the person the draft card is ready to create, edit or reject, and end your turn.",
    })


__all__ = ["DRAFT_TOOL_NAMES", "DRAFT_TOOL_SCHEMAS", "call_draft_tool"]
