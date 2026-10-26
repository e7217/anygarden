"""Campaign retrospectives (#930 §3.6, Phase 6, layer ③).

A retrospective cycle turns what the campaign learned into lasting agent
know-how: its AGENTS.md and skills. Per decision 6 the change is applied
**without a person's approval** and recorded instead:

- an instructions revision with ``source="campaign"`` and
  ``context={campaign_id, attempt_id, rationale, …}`` (restorable through
  the existing revision restore),
- a ``campaign_events(instructions_changed)`` row,
- a summary message in the campaign's room.

Safety limits: at most ``MAX_CHANGES_PER_RETROSPECTIVE`` changes per
retrospective, each ≤ ``MAX_CHANGE_BYTES``; AGENTS.md changes only replace
the body of the ``## Campaign learnings`` section (created at the end when
absent — the rest of the file is never rewritten); a skill is either new or
one this campaign created earlier.

Writes go through ``instructions.service.write_instructions``, so the
agent's ``instructions_mode`` applies: on a disk-sourced machine the change
is made on disk through the machine bus; while that machine is offline the
change fails — the failure is recorded and the retrospective goes on.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.campaigns.agent_actions import CampaignToolError, CycleContext
from anygarden.campaigns.notify import mark
from anygarden.campaigns.service import Clock, record_event, utcnow
from anygarden.db.models import Agent, CampaignEvent, Message

LEARNINGS_HEADING = "## Campaign learnings"
MAX_CHANGES_PER_RETROSPECTIVE = 3
MAX_CHANGE_BYTES = 16 * 1024
RATIONALE_MAX = 2000
TARGETS = frozenset({"agents_md", "skill"})
_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SECTION_BREAK = re.compile(r"^#{1,2}\s")


def skill_path(name: str) -> str:
    return f"skills/{name}/SKILL.md"


def replace_learnings_section(agents_md: str, body: str) -> str:
    """Return *agents_md* with the ``## Campaign learnings`` body set to
    *body*; everything outside that section is kept byte for byte."""
    lines = agents_md.split("\n") if agents_md else []
    body_lines = body.strip("\n").split("\n") if body.strip() else []
    start = next((i for i, line in enumerate(lines) if line.strip() == LEARNINGS_HEADING), None)
    if start is None:
        prefix = list(lines)
        while prefix and not prefix[-1].strip():
            prefix.pop()
        return "\n".join([*prefix, *([""] if prefix else []), LEARNINGS_HEADING, *body_lines])
    end = next(
        (j for j in range(start + 1, len(lines)) if _SECTION_BREAK.match(lines[j])), len(lines)
    )
    rest = lines[end:]
    return "\n".join([*lines[: start + 1], *body_lines, *([""] + rest if rest else [])])


def _check_section_body(text: str) -> None:
    for line in text.split("\n"):
        if _SECTION_BREAK.match(line):
            raise CampaignToolError(
                "CAMPAIGN_LEARNING_OUTSIDE_SECTION",
                f"text may not start another top-level section ({line.strip()[:60]!r}); "
                f"it is the body of {LEARNINGS_HEADING} only — use ### sub-headings",
            )


async def _learning_events(db: AsyncSession, campaign_id: str) -> list[CampaignEvent]:
    return list(await db.scalars(
        select(CampaignEvent).where(
            CampaignEvent.campaign_id == campaign_id,
            CampaignEvent.type == "instructions_changed",
        ).order_by(CampaignEvent.created_at)
    ))


async def campaign_created_skills(db: AsyncSession, campaign_id: str) -> set[str]:
    """Skill paths this campaign's retrospectives created."""
    return {
        (event.details or {}).get("path")
        for event in await _learning_events(db, campaign_id)
        if (event.details or {}).get("created") and (event.details or {}).get("target") == "skill"
    }


async def apply_learning(
    db: AsyncSession,
    bus: Any,
    ctx: CycleContext,
    *,
    target: Any,
    text: Any,
    rationale: Any,
    name: Any = None,
    clock: Clock = utcnow,
) -> tuple[dict[str, Any], list[Message]]:
    """``apply_campaign_learning``. Commits (through ``write_instructions``).

    Returns the tool payload and the room messages to broadcast.
    """
    from anygarden.instructions.service import current_snapshot, write_instructions
    from anygarden.messages.service import append_message

    campaign, attempt = ctx.campaign, ctx.attempt
    if attempt.kind != "retrospective":
        raise CampaignToolError(
            "CAMPAIGN_NOT_RETROSPECTIVE",
            "Instructions change only in a retrospective cycle",
        )
    if target not in TARGETS:
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "target must be agents_md or skill")
    if not isinstance(text, str) or not text.strip():
        raise CampaignToolError("CAMPAIGN_INVALID_ARGUMENT", "text is required")
    if len(text.encode("utf-8")) > MAX_CHANGE_BYTES:
        raise CampaignToolError(
            "CAMPAIGN_LEARNING_TOO_LARGE", f"text is larger than {MAX_CHANGE_BYTES // 1024}KB"
        )
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > RATIONALE_MAX:
        raise CampaignToolError(
            "CAMPAIGN_INVALID_ARGUMENT", f"rationale is required (≤ {RATIONALE_MAX} chars)"
        )
    rationale = rationale.strip()
    events = await _learning_events(db, campaign.id)
    done = [e for e in events if (e.details or {}).get("attempt_id") == attempt.id]
    if len(done) >= MAX_CHANGES_PER_RETROSPECTIVE:
        raise CampaignToolError(
            "CAMPAIGN_LEARNING_LIMIT",
            f"A retrospective makes at most {MAX_CHANGES_PER_RETROSPECTIVE} changes",
        )
    agent = await db.get(Agent, campaign.agent_id)
    if agent is None:  # pragma: no cover — guarded by current_cycle
        raise CampaignToolError("CAMPAIGN_AGENT_MISMATCH", "The campaign's agent is gone")
    snapshot = await current_snapshot(db, agent)
    created = False
    path: str | None = None
    if target == "agents_md":
        _check_section_body(text)
        new_text = replace_learnings_section(snapshot.agents_md, text)
        operation = "write_agents_md"
        if new_text.strip() == snapshot.agents_md.strip():
            return ({"campaign_id": campaign.id, "changed": False,
                     "note": "AGENTS.md already says this; nothing changed."}, [])
    else:
        if not isinstance(name, str) or not _SKILL_NAME.match(name):
            raise CampaignToolError(
                "CAMPAIGN_INVALID_ARGUMENT",
                "name must be a skill name (lowercase letters, digits, - or _)",
            )
        path = skill_path(name)
        created = path not in snapshot.skills
        owned = {
            (e.details or {}).get("path") for e in events
            if (e.details or {}).get("created") and (e.details or {}).get("target") == "skill"
        }
        if not created and path not in owned:
            raise CampaignToolError(
                "CAMPAIGN_SKILL_NOT_OWNED",
                f"{path} was not created by this campaign; create a new skill instead",
            )
        new_text = text
        operation = "write_skill"
    context = {
        "campaign_id": campaign.id, "attempt_id": attempt.id, "cycle_no": attempt.cycle_no,
        "rationale": rationale, "target": target, "path": path,
    }
    messages: list[Message] = []
    what = "AGENTS.md (" + LEARNINGS_HEADING.lstrip("# ") + ")" if target == "agents_md" else (
        f"{'new skill' if created else 'skill'} `{path}`"
    )

    async def before_commit(revision: int) -> None:
        await record_event(
            db, campaign.id,
            key=f"campaign:{campaign.id}:attempt:{attempt.id}:learning:{len(done) + 1}",
            type="instructions_changed",
            details={**context, "created": created, "revision": revision},
            actor=f"agent:{campaign.agent_id}", clock=clock,
        )
        mark(db, campaign_id=campaign.id)
        messages.append(await append_message(
            db, campaign.room_id, None,
            f"Campaign “{campaign.title}” retrospective updated {what} "
            f"(instructions revision {revision}, no approval needed). Why: {rationale}\n"
            "Restore an earlier revision from the agent's instructions history if needed.",
            {"kind": "campaign_learning", "campaign_id": campaign.id,
             "attempt_id": attempt.id, "revision": revision},
        ))

    actor = f"agent:{campaign.agent_id}"
    try:
        revision = await write_instructions(
            db, bus, agent, operation=operation, by_user_id=None, path=path,
            text=new_text, source="campaign", context=context, before_commit=before_commit,
        )
    except HTTPException as exc:
        # The rollback expires the loaded rows; only plain values are used
        # from here on.
        campaign_id, attempt_id = context["campaign_id"], context["attempt_id"]
        await db.rollback()
        await record_event(
            db, campaign_id,
            key=f"campaign:{campaign_id}:attempt:{attempt_id}:learning_failed:{clock().timestamp()}",
            type="instructions_change_failed",
            details={**context, "error": str(exc.detail)},
            actor=actor, clock=clock,
        )
        await db.commit()
        raise CampaignToolError(
            "CAMPAIGN_LEARNING_FAILED",
            f"{exc.detail} The failure is recorded; the retrospective can still finish.",
        ) from None
    return ({
        "campaign_id": campaign.id,
        "changed": True,
        "target": target,
        "path": path,
        "created": created,
        "revision": revision,
        "changes_left": MAX_CHANGES_PER_RETROSPECTIVE - len(done) - 1,
    }, messages)


__all__ = [
    "LEARNINGS_HEADING",
    "MAX_CHANGES_PER_RETROSPECTIVE",
    "MAX_CHANGE_BYTES",
    "apply_learning",
    "campaign_created_skills",
    "replace_learnings_section",
    "skill_path",
]
