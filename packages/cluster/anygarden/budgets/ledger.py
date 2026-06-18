"""Token-cost ledger + invocation-block evaluation (#453, Wave 1d).

This module turns the *measured* ``LLMGatewayUsage`` stream (one row per
relayed LLM call, written by the reverse proxy) into a budget decision:

- :func:`compute_observed_tokens` — the window SUM of prompt+completion
  tokens for a scope. ``status_code < 400`` is filtered out so the very
  429 refusal rows this feature writes can never inflate the sum and
  trap a scope in a permanent block.
- :func:`evaluate_invocation_block` — loads the *active*,
  ``hard_stop_enabled`` policies for the global / agent / room scopes and
  returns the first one whose observed tokens have reached its ceiling,
  or ``None`` (the no-block path). A small in-process TTL cache over the
  per-scope SUM keeps the hot proxy path from issuing a DB SUM on every
  single LLM call; a few seconds of window slop is acceptable (same
  philosophy as the in-memory rate limiters).

Default-OFF invariant: ``hard_stop_enabled`` defaults to False and there
are no policies on a fresh DB, so :func:`evaluate_invocation_block`
returns ``None`` for every call until an admin creates and enables a
policy. Merging this code changes no runtime behaviour.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import LLMGatewayUsage, TokenBudgetPolicy

__all__ = [
    "InvocationBlock",
    "compute_observed_tokens",
    "evaluate_invocation_block",
    "clear_observed_cache",
]


# How long a per-scope observed-token SUM stays cached before the hot
# path re-reads it from the DB. A few seconds of slop on a 24h window is
# immaterial; the DB remains the source of truth.
_CACHE_TTL_SECONDS = 8.0

# Process-local cache: (scope_type, scope_id) -> (observed, expires_monotonic).
_observed_cache: dict[tuple[str, Optional[str]], tuple[int, float]] = {}


def clear_observed_cache() -> None:
    """Drop the in-process observed-token cache.

    Test hook — production relies purely on TTL expiry.
    """
    _observed_cache.clear()


@dataclass(frozen=True)
class InvocationBlock:
    """A scope that is over its hard-stop ceiling for the current window."""

    scope_type: str
    scope_id: Optional[str]
    reason: str


def _window_start(window_kind: str, *, now: Optional[datetime] = None) -> datetime:
    """Return the inclusive lower bound of the budget window.

    - ``calendar_day_utc`` — midnight UTC today.
    - anything else (``rolling_24h`` default) — ``now - 24h``.
    """
    current = now or datetime.now(timezone.utc)
    if window_kind == "calendar_day_utc":
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    # rolling_24h (default / unknown) — last 24 hours.
    from datetime import timedelta

    return current - timedelta(hours=24)


async def compute_observed_tokens(
    session: AsyncSession,
    *,
    scope_type: str,
    scope_id: Optional[str],
    window_start: datetime,
) -> int:
    """Sum observed prompt+completion tokens for a scope within a window.

    ``coalesce`` handles the nullable token columns (a streaming parse
    that failed to extract usage leaves them NULL — counting them as 0
    avoids undercounting from crashing into a NoneType sum). The
    ``status_code < 400`` filter excludes refusals/errors — crucially the
    429 rows this feature itself writes, which would otherwise inflate
    the sum on every blocked call and make the block self-perpetuating.
    """
    stmt = select(
        func.coalesce(
            func.sum(
                func.coalesce(LLMGatewayUsage.prompt_tokens, 0)
                + func.coalesce(LLMGatewayUsage.completion_tokens, 0)
            ),
            0,
        )
    ).where(
        LLMGatewayUsage.timestamp >= window_start,
        LLMGatewayUsage.status_code < 400,
    )

    if scope_type == "agent":
        stmt = stmt.where(LLMGatewayUsage.agent_id == scope_id)
    elif scope_type == "room":
        stmt = stmt.where(LLMGatewayUsage.room_id == scope_id)
    # scope_type == "global" — no id filter; sums all usage.

    total = (await session.execute(stmt)).scalar_one()
    return int(total or 0)


async def _observed_for_policy(
    session: AsyncSession,
    policy: TokenBudgetPolicy,
    *,
    now: datetime,
) -> int:
    """Observed tokens for ``policy``'s scope+window, via the TTL cache."""
    key = (policy.scope_type, policy.scope_id)
    monotonic_now = time.monotonic()
    cached = _observed_cache.get(key)
    if cached is not None and cached[1] > monotonic_now:
        return cached[0]

    window_start = _window_start(policy.window_kind, now=now)
    observed = await compute_observed_tokens(
        session,
        scope_type=policy.scope_type,
        scope_id=policy.scope_id,
        window_start=window_start,
    )
    _observed_cache[key] = (observed, monotonic_now + _CACHE_TTL_SECONDS)
    return observed


async def evaluate_invocation_block(
    session_factory: Any,
    *,
    agent_id: Optional[str],
    room_id: Optional[str],
) -> Optional[InvocationBlock]:
    """Return the first over-ceiling hard-stop block, or ``None``.

    Loads *active* policies with ``hard_stop_enabled`` for the scopes that
    apply to this call:

    - ``global`` — always.
    - ``(agent, agent_id)`` — when ``agent_id`` is known.
    - ``(room, room_id)`` — only when ``room_id`` is known (best-effort;
      the proxy resolves it from tracing's in-flight map, which may be
      empty when tracing is disabled).

    For each, the observed-token SUM for its window is compared against
    ``token_ceiling``; the first scope at/over its ceiling wins. Returns
    ``None`` when no policy applies, all are under ceiling, or none are
    active hard-stop policies — the default-OFF no-op path.
    """
    # Build the scope predicate. global always; agent/room only when the
    # id is known. ``None`` agent/room means we simply don't query those
    # scope rows (an agent-less caller still hits global policies).
    scope_clauses = [
        (TokenBudgetPolicy.scope_type == "global")
    ]
    if agent_id is not None:
        scope_clauses.append(
            (TokenBudgetPolicy.scope_type == "agent")
            & (TokenBudgetPolicy.scope_id == agent_id)
        )
    if room_id is not None:
        scope_clauses.append(
            (TokenBudgetPolicy.scope_type == "room")
            & (TokenBudgetPolicy.scope_id == room_id)
        )

    from sqlalchemy import or_

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(TokenBudgetPolicy).where(
                    TokenBudgetPolicy.is_active.is_(True),
                    TokenBudgetPolicy.hard_stop_enabled.is_(True),
                    or_(*scope_clauses),
                )
            )
        ).scalars().all()

        if not rows:
            return None

        # Deterministic evaluation order: global, then agent, then room —
        # so a global cap is reported before a narrower one when both are
        # tripped. (Any single over-ceiling scope blocks the call.)
        order = {"global": 0, "agent": 1, "room": 2}
        policies = sorted(rows, key=lambda p: order.get(p.scope_type, 99))

        now = datetime.now(timezone.utc)
        for policy in policies:
            observed = await _observed_for_policy(session, policy, now=now)
            if observed >= policy.token_ceiling:
                return InvocationBlock(
                    scope_type=policy.scope_type,
                    scope_id=policy.scope_id,
                    reason=(
                        f"token budget exceeded: {observed} >= "
                        f"{policy.token_ceiling} ({policy.window_kind})"
                    ),
                )

    return None
