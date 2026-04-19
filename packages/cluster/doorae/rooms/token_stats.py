"""Per-room token usage telemetry (#157 Phase C).

Aggregates ``Message`` rows into a rolling-window view so admins can
observe how aggressively a room (and each agent in it) is consuming
tokens. The 2026-04-19 deep-research report notes that Anthropic's
multi-agent research system runs ~15x the tokens of a single chat;
in rooms where agent-to-agent conversation is enabled (Plan 1, #159)
individual runs can diverge quickly and operators need a live view
before deciding whether to tune limits or activate a future
auto-cutoff (conditional follow-up R7).

The token count is a **conservative estimate** — ``len(content) // 4``
per message, a stand-in until per-engine tokenisers are wired in.
Accurate accounting is future work; this module's job is to surface
trend and per-agent skew, not final billing numbers.

Response schema for ``/api/v1/rooms/{id}/token-stats``::

    {
      "window_1h": {
        "tokens": 12500,
        "messages": 47,
        "agents": 3,
        "per_agent": [
          {
            "participant_id": "...",
            "agent_name": "Researcher",
            "tokens": 8100,
            "messages": 22,
            "last_active_at": "2026-04-19T10:30:00+00:00",
          },
          ...
        ],
      },
      "window_24h": { /* same shape */ },
    }

``per_agent`` lets #159 Phase D render a per-agent badge / drawer
panel without repeating the aggregation client-side.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Agent, Message, Participant


def estimate_tokens(content: str) -> int:
    """Conservative token estimate for arbitrary text.

    ``len(content) // 4`` is a well-known floor for English; for
    Korean it tends to under-count by ~20-40% but the trend stays
    monotonic, which is what the dashboard needs. Minimum of 1
    avoids zero-token rows for single-char messages.
    """
    if not content:
        return 1
    return max(1, len(content) // 4)


@dataclass(frozen=True)
class AgentUsage:
    participant_id: str
    agent_name: str | None
    tokens: int
    messages: int
    last_active_at: datetime | None


@dataclass(frozen=True)
class WindowStats:
    tokens: int
    messages: int
    agents: int
    per_agent: list[AgentUsage]


# Default windows keyed by the exact JSON label the UI / #159 Phase D
# consumes. Keeping labels explicit (rather than derived from timedelta)
# avoids surprises like timedelta(hours=24) collapsing into "1d".
DEFAULT_WINDOWS: tuple[tuple[str, timedelta], ...] = (
    ("window_1h", timedelta(hours=1)),
    ("window_24h", timedelta(hours=24)),
)


async def get_room_token_stats(
    session: AsyncSession,
    room_id: str,
    *,
    windows: tuple[tuple[str, timedelta], ...] = DEFAULT_WINDOWS,
    now: datetime | None = None,
) -> dict[str, WindowStats]:
    """Compute per-window token stats for ``room_id``.

    Returns a dict keyed by the explicit window label (e.g.
    ``"window_1h"``, ``"window_24h"``). Per-agent rows carry
    ``agent_name`` pulled via Participant → Agent; messages whose
    participant is a user (no Agent row) are aggregated with
    ``agent_name=None`` so the caller can distinguish the two.
    """
    now = now or datetime.now(tz=timezone.utc)
    results: dict[str, WindowStats] = {}
    for label, delta in windows:
        cutoff = now - delta
        results[label] = await _collect_window(session, room_id, cutoff)
    return results


async def _collect_window(
    session: AsyncSession,
    room_id: str,
    cutoff: datetime,
) -> WindowStats:
    stmt = (
        select(
            Message.participant_id,
            Message.content,
            Message.created_at,
            Participant.agent_id,
            Agent.name,
        )
        .select_from(Message)
        .join(Participant, Participant.id == Message.participant_id, isouter=True)
        .join(Agent, Agent.id == Participant.agent_id, isouter=True)
        .where(Message.room_id == room_id)
        .where(Message.created_at >= cutoff)
    )
    rows = (await session.execute(stmt)).all()

    per_agent: dict[str, dict[str, Any]] = {}
    total_tokens = 0
    total_messages = 0
    for pid, content, created_at, agent_id, agent_name in rows:
        if pid is None:
            continue
        tokens = estimate_tokens(content or "")
        total_tokens += tokens
        total_messages += 1
        slot = per_agent.setdefault(
            pid,
            {
                "participant_id": pid,
                "agent_name": agent_name,
                "tokens": 0,
                "messages": 0,
                "last_active_at": None,
            },
        )
        slot["tokens"] += tokens
        slot["messages"] += 1
        # Track latest timestamp per agent
        if slot["last_active_at"] is None or (
            created_at and created_at > slot["last_active_at"]
        ):
            slot["last_active_at"] = created_at

    # Materialise, sort by tokens desc for stable UI ordering
    rows_out = sorted(
        (
            AgentUsage(
                participant_id=entry["participant_id"],
                agent_name=entry["agent_name"],
                tokens=entry["tokens"],
                messages=entry["messages"],
                last_active_at=entry["last_active_at"],
            )
            for entry in per_agent.values()
        ),
        key=lambda u: u.tokens,
        reverse=True,
    )
    return WindowStats(
        tokens=total_tokens,
        messages=total_messages,
        agents=len(per_agent),
        per_agent=rows_out,
    )


def serialise_window(stats: WindowStats) -> dict[str, Any]:
    """Translate a :class:`WindowStats` into the JSON response shape."""
    return {
        "tokens": stats.tokens,
        "messages": stats.messages,
        "agents": stats.agents,
        "per_agent": [
            {
                "participant_id": u.participant_id,
                "agent_name": u.agent_name,
                "tokens": u.tokens,
                "messages": u.messages,
                "last_active_at": (
                    u.last_active_at.isoformat() if u.last_active_at else None
                ),
            }
            for u in stats.per_agent
        ],
    }
