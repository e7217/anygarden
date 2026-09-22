"""Engine-neutral usage stream for AnyGarden (#655).

The measured usage rows outlive any single execution engine or gateway: both
the LLM gateway reverse-proxy and the gateway-free CLI-engine path (WS
handler) write through :func:`write_usage_row` here. The physical storage is
the ``usage_ledger`` table (migration 074).
"""

from __future__ import annotations

from typing import Any

import structlog

from anygarden.db.models import UsageLedger

logger = structlog.getLogger(__name__)

__all__ = ["write_usage_row"]


async def write_usage_row(
    session_factory: Any,
    *,
    identity_kind: str,
    identity_id: str,
    agent_id: str | None,
    model_name: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    duration_ms: int,
    status_code: int,
    error: str | None = None,
    room_id: str | None = None,
    cost_usd: float | None = None,
) -> None:
    """Persist one usage row. Safe to call as a fire-and-forget task.

    Swallows exceptions so a DB hiccup can't poison the caller — the
    HTTP response (or WS frame handling) has already completed by the
    time this runs. ``room_id`` is filled from the tracing correlation
    (#420) when the call could be tied to a single in-flight request;
    it stays ``None`` otherwise.

    ``cost_usd`` is the per-request USD cost. Gateway-routed callers
    (openhands via the reverse proxy) leave this ``None`` — the proxy
    has no provider-cost signal. CLI engines that self-report a cost
    populate it: claude-code stamps its SDK's ``total_cost_usd`` (an
    *estimate*, not a provider invoice); codex / gemini report no cost
    and stay NULL. Admin usage aggregation sums it nullable-safe.
    """
    try:
        async with session_factory() as db:
            db.add(
                UsageLedger(
                    identity_kind=identity_kind,
                    identity_id=identity_id,
                    agent_id=agent_id,
                    room_id=room_id,
                    model_name=model_name or "",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    error=error,
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("anygarden.usage.write_failed", error=str(exc))
