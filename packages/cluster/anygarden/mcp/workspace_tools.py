"""``workspace_query`` / ``workspace_action``: the workspace by conversation (#938).

Listed only for agents with ``workspace_tools`` on. Each call acts for the
person whose message started the turn (``workspace.requester``), with
that person's rights. ``ask`` actions wait for that person's approval in
the room. Logic lives in ``anygarden.workspace``.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from anygarden.mcp.project_tools import _tool
from anygarden.workspace import policy as p
from anygarden.workspace.queries import QUERY_KINDS


def _action_lines() -> str:
    lines = []
    for spec in p.ACTIONS.values():
        if spec.tier == "off":
            continue
        fields = ", ".join(spec.params.model_fields)
        when = "runs now" if spec.tier == "allowed" else "needs approval"
        lines.append(f"- {spec.name} ({when}; params: {fields}): {spec.summary}")
    return "\n".join(lines)


WORKSPACE_TOOL_SCHEMAS = [
    _tool(
        "workspace_query",
        "Look over the workspace as the person who wrote to you sees it. kinds: "
        "projects, rooms, agents, machines (no room_id); room (members), tasks, routines "
        "(recurring work), goals (metric goals) for one room_id. Use it to find ids before "
        "workspace_action. Answers only what that person may see.",
        {
            "kind": {"type": "string", "enum": list(QUERY_KINDS)},
            "room_id": {"type": "string", "minLength": 1},
            "status": {"type": "string", "minLength": 1, "maxLength": 32},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        ["kind"],
    ),
    _tool(
        "workspace_action",
        "Change the workspace for the person who wrote to you, with their rights. Only do "
        "what they asked for. Actions that need approval post a card in the room and return "
        "status=pending_approval: tell the person, end your turn and do not call again — you "
        "are woken with the outcome. Refusals explain why; do not work around them.\n"
        + _action_lines(),
        {
            "action": {"type": "string", "enum": [n for n, s in p.ACTIONS.items() if s.tier != "off"]},
            "params": {"type": "object"},
        },
        ["action", "params"],
    ),
]
WORKSPACE_TOOL_NAMES = frozenset(tool["name"] for tool in WORKSPACE_TOOL_SCHEMAS)


def _ok(payload: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
        "structuredContent": payload,
        "isError": False,
    }


def _error(code: str, detail: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"{code}: {detail}"}],
        "structuredContent": {"code": code, "detail": detail},
        "isError": True,
    }


async def workspace_tools_enabled(session_factory, agent_id: str) -> bool:
    from anygarden.db.models import Agent

    async with session_factory() as db:
        agent = await db.get(Agent, agent_id)
        return bool(agent and agent.workspace_tools)


async def call_workspace_tool(request: Request, *, agent_id: str, name: str, arguments: dict) -> dict:
    from anygarden.mcp.project_tools import broadcast_project_messages, turn_proof
    from anygarden.rooms.authorization import room_authorization_session
    from anygarden.workspace import approvals
    from anygarden.workspace.actions import ActionFailed, parse_params, run_action
    from anygarden.workspace.queries import run_query
    from anygarden.workspace.requester import RequesterRefused, requester_for_turn

    session_factory = request.app.state.session_factory
    if not await workspace_tools_enabled(session_factory, agent_id):
        return _error("WORKSPACE_TOOLS_OFF", "Workspace tools are not enabled for this agent")
    try:
        proof = turn_proof(request)
    except HTTPException as exc:
        return _error("TURN_PROOF_REQUIRED", str(exc.detail))
    # Resolve whose rights to use in its own session: the turn fence takes
    # row locks that the action's own session must not wait on.
    try:
        async with session_factory() as db:
            requester = await requester_for_turn(db, agent_id=agent_id, proof=proof)
            await db.rollback()
    except RequesterRefused as exc:
        return _error(exc.code, exc.detail)

    try:
        async with room_authorization_session(session_factory) as db:
            if name == "workspace_query":
                unknown = set(arguments) - {"kind", "room_id", "status", "limit"}
                if unknown:
                    return _error("INVALID_PARAMS", f"unknown arguments: {', '.join(sorted(unknown))}")
                return _ok(await run_query(db, request, requester.identity, **arguments))

            action = arguments.get("action")
            if not isinstance(action, str):
                return _error("INVALID_PARAMS", "action is required")
            params = parse_params(action, arguments.get("params"))
            spec = p.ACTIONS[action]
            if spec.tier == "allowed":
                result = await run_action(db, request, requester.identity, action, params, agent_id=agent_id)
                return _ok({"status": "done", "action": action, "result": result})

            await _check_proposal(db, agent_id, action, params)
            row, message = await approvals.open_request(
                db, requester=requester, agent_id=agent_id, action=action,
                params=params.model_dump(mode="json"),
            )
            request_id = row.id
            await db.commit()
            if message is not None:
                await broadcast_project_messages(db, request=request, messages=[message])
            return _ok({
                "status": "pending_approval",
                "request_id": request_id,
                "approver": "an admin" if spec.admin_only else "the person who asked, or an admin",
                "next": "Tell the person it is waiting for approval and end your turn.",
            })
    except ActionFailed as exc:
        return _error(exc.code, exc.detail)


async def _check_proposal(db, agent_id: str, action: str, params) -> None:
    """Refuse a proposal that could never be approved as asked."""

    from pydantic import ValidationError

    from anygarden.db.models import Agent
    from anygarden.workspace.actions import ActionFailed, _validation_text

    if action == "create_goal":
        from anygarden.api.v1.campaigns import CampaignCreate

        try:
            CampaignCreate.model_validate(params.spec)
        except ValidationError as exc:
            raise ActionFailed("INVALID_PARAMS", "spec: " + _validation_text(exc)) from None
        return
    if action == "create_routine":
        from anygarden.goals.policy import InvalidTriggerConfig, validate_trigger_config

        try:
            validate_trigger_config(params.trigger_type, params.trigger_config)
        except InvalidTriggerConfig as exc:
            raise ActionFailed("INVALID_PARAMS", f"trigger_config: {exc}") from None
        return
    if action != "hire_agent":
        return
    creator = await db.get(Agent, agent_id)
    if creator is None or not creator.can_create_agents:
        raise ActionFailed("HIRE_NOT_ALLOWED", "This agent may not propose new agents; an admin can allow it")
    if params.permission_level is not None and (
        p.permission_rank(params.permission_level) > p.permission_rank(creator.permission_level)
    ):
        raise ActionFailed(
            "PERMISSION_ABOVE_CREATOR",
            f"A new agent cannot have more permission than you ({creator.permission_level or 'standard'})",
        )


__all__ = ["WORKSPACE_TOOL_NAMES", "WORKSPACE_TOOL_SCHEMAS", "call_workspace_tool", "workspace_tools_enabled"]
