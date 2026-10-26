"""Lease-authenticated campaign tools for a campaign's agent (#930 §3.5).

Same calling convention as ``mcp/project_tools.py``: the turn proof
headers authorize the call (``authorize_turn``), the tool runs in its own
session and the result is an MCP ``tools/call`` result. The tools act on
the campaign cycle the current turn works on; they take no campaign ids.
Logic lives in ``anygarden.campaigns.agent_actions`` /
``anygarden.campaigns.retrospective``.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from anygarden.mcp.project_tools import _tool

_string = {"type": "string", "minLength": 1}

CAMPAIGN_TOOL_SCHEMAS = [
    _tool(
        "record_campaign_attempt",
        "Record what you tried in this campaign cycle. Call it exactly once per cycle, "
        "before you finish the run with complete_project_execution; a second call or a "
        "call for an earlier cycle is refused. Report only what you did and saw — the "
        "system runs the evaluator and decides the score. outcome=attempted when you made "
        "a change to be evaluated, abandoned when you gave up on this cycle's idea. "
        "The response carries follow_up: instructions people gave since the cycle opened "
        "(changed purpose, target, deadline or budget, a pause). Read them before you "
        "finish.",
        {
            "outcome": {"type": "string", "enum": ["attempted", "abandoned"]},
            "hypothesis": {**_string, "maxLength": 4000,
                           "description": "What you expected to improve the metric, and why"},
            "change_summary": {**_string, "maxLength": 8000,
                               "description": "What you actually changed"},
            "artifact_ref": {**_string, "maxLength": 1024,
                             "description": "Commit, branch, file or artifact that holds it"},
            "lessons": {**_string, "maxLength": 4000,
                        "description": "What the next cycle should know"},
        },
        ["outcome", "hypothesis", "change_summary"],
    ),
    _tool(
        "schedule_campaign_wakeup",
        "Ask for the next campaign cycle to start no earlier than a time — for waiting on "
        "something outside (a long job, an external result). Give delay_seconds "
        "(60 s to 7 days) or at (ISO timestamp), before the campaign deadline. The next "
        "cycle opens at max(this cycle's close + 5 min, the wake time). The last call in "
        "a cycle wins. Then finish the cycle as usual instead of idling.",
        {
            "delay_seconds": {"type": "integer", "minimum": 60, "maximum": 604800},
            "at": {"type": "string", "format": "date-time"},
            "reason": {**_string, "maxLength": 500},
        },
        ["reason"],
    ),
    _tool(
        "report_campaign_blocked",
        "Report that the campaign cannot progress without a person (missing access, a "
        "broken evaluator, a decision only a person can make). The campaign becomes "
        "blocked when this cycle closes and waits for a person to resume it. Say what "
        "would unblock you.",
        {
            "reason": {**_string, "maxLength": 4000},
            "unblock_hint": {**_string, "maxLength": 4000},
        },
        ["reason", "unblock_hint"],
    ),
    _tool(
        "read_campaign_attempts",
        "Read this campaign's earlier attempts, newest first: hypothesis, change, "
        "system score, outcome, lessons. The cycle brief shows only the last five.",
        {
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
        },
        [],
    ),
    _tool(
        "apply_campaign_learning",
        "Retrospective cycles only: turn a lesson into lasting know-how. "
        "target=agents_md sets the body of the '## Campaign learnings' section of your "
        "AGENTS.md (the section is created if missing; the rest of the file is never "
        "changed; text replaces the section body, so include the learnings to keep, and "
        "use ### sub-headings only). target=skill writes skills/<name>/SKILL.md — a new "
        "skill, or one this campaign created. At most 3 changes per retrospective, 16KB "
        "each. Applied without approval and recorded as a restorable revision with your "
        "rationale.",
        {
            "target": {"type": "string", "enum": ["agents_md", "skill"]},
            "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$",
                     "description": "Skill name (target=skill)"},
            "text": {**_string, "maxLength": 16384},
            "rationale": {**_string, "maxLength": 2000},
        },
        ["target", "text", "rationale"],
    ),
]
CAMPAIGN_TOOL_NAMES = frozenset(tool["name"] for tool in CAMPAIGN_TOOL_SCHEMAS)


def _error(code: str, detail: str) -> dict:
    return {
        "content": [{"type": "text", "text": f"{code}: {detail}"}],
        "structuredContent": {"code": code, "detail": detail},
        "isError": True,
    }


async def call_campaign_tool(request: Request, *, agent_id: str, name: str, arguments: dict) -> dict:
    from anygarden.campaigns import agent_actions
    from anygarden.campaigns.agent_actions import CampaignToolError
    from anygarden.campaigns.notify import commit_and_publish
    from anygarden.mcp.project_tools import broadcast_project_messages, turn_proof
    from anygarden.project_executions.service import ExecutionConflict

    manager = getattr(request.app.state, "connection_manager", None)
    try:
        proof = turn_proof(request)
        async with request.app.state.session_factory() as db:
            ctx = await agent_actions.current_cycle(db, agent_id=agent_id, proof=proof)
            messages: list = []
            if name == "record_campaign_attempt":
                payload = await agent_actions.record_attempt(db, ctx, **arguments)
            elif name == "schedule_campaign_wakeup":
                payload = await agent_actions.schedule_wakeup(db, ctx, **arguments)
            elif name == "report_campaign_blocked":
                payload = await agent_actions.report_blocked(db, ctx, **arguments)
            elif name == "read_campaign_attempts":
                payload = await agent_actions.read_attempts(db, ctx, **arguments)
            elif name == "apply_campaign_learning":
                from anygarden.campaigns.retrospective import apply_learning

                payload, messages = await apply_learning(
                    db, getattr(request.app.state, "machine_bus", None), ctx, **arguments,
                )
            else:  # pragma: no cover — the router only routes known names
                raise HTTPException(400, "Unknown campaign tool")
            await commit_and_publish(db, manager)
            if messages:
                await broadcast_project_messages(db, request=request, messages=messages)
        return {"content": [{"type": "text", "text": str(payload)}],
                "structuredContent": payload, "isError": False}
    except HTTPException as exc:
        return {"content": [{"type": "text", "text": str(exc.detail)}], "isError": True}
    except CampaignToolError as exc:
        return _error(exc.code, exc.detail)
    except ExecutionConflict as exc:
        return _error(exc.code, exc.detail)
    except TypeError as exc:
        # Unknown or missing arguments.
        return _error("CAMPAIGN_INVALID_ARGUMENT", str(exc))


__all__ = ["CAMPAIGN_TOOL_NAMES", "CAMPAIGN_TOOL_SCHEMAS", "call_campaign_tool"]
