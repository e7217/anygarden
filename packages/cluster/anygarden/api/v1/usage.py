"""Admin-only usage aggregation from the neutral durable ledger."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.db.models import UsageLedger
from anygarden.dependencies import get_admin_identity, get_db

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])


class UsageBucket(BaseModel):
    key: str
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    # #461 (Wave 2d) — summed USD cost for the bucket. Nullable-safe at
    # the SQL layer (``coalesce(sum(cost_usd), 0)``); rows with no cost
    # signal contribute 0.
    cost_usd: float = 0.0


class UsageOut(BaseModel):
    window_hours: int
    total_requests: int
    # #461 — grand-total USD cost across the window (sum of self-reported
    # per-request reported costs; this is not an invoice).
    total_cost_usd: float = 0.0
    by_model: list[UsageBucket]
    by_agent: list[UsageBucket]


@router.get("", response_model=UsageOut)
async def get_usage(
    window: str = "24h",
    identity: Identity = Depends(get_admin_identity),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
) -> UsageOut:
    """Aggregate usage counters from ``UsageLedger`` within ``window``.

    ``window`` accepts ``Nh`` / ``Nd`` (hours / days). Out-of-range or
    unparseable values fall back to 24h rather than 400 — admin UI
    trusts server defaults.
    """
    hours = _parse_window(window)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    total = (
        await db.execute(
            select(func.count(UsageLedger.id)).where(UsageLedger.timestamp >= since)
        )
    ).scalar_one()

    # #461 — grand-total USD cost (nullable-safe). Rows with no cost
    # signal contribute 0 via coalesce.
    total_cost = (
        await db.execute(
            select(func.coalesce(func.sum(UsageLedger.cost_usd), 0.0)).where(
                UsageLedger.timestamp >= since
            )
        )
    ).scalar_one()

    by_model_rows = (
        await db.execute(
            select(
                UsageLedger.model_name,
                func.count(UsageLedger.id),
                func.coalesce(func.sum(UsageLedger.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLedger.completion_tokens), 0),
                # #461 — nullable-safe USD cost sum per model.
                func.coalesce(func.sum(UsageLedger.cost_usd), 0.0),
            )
            .where(UsageLedger.timestamp >= since)
            .group_by(UsageLedger.model_name)
            .order_by(func.count(UsageLedger.id).desc())
        )
    ).all()

    by_agent_rows = (
        await db.execute(
            select(
                UsageLedger.agent_id,
                func.count(UsageLedger.id),
                func.coalesce(func.sum(UsageLedger.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLedger.completion_tokens), 0),
                # #461 — nullable-safe USD cost sum per agent.
                func.coalesce(func.sum(UsageLedger.cost_usd), 0.0),
            )
            .where(
                UsageLedger.timestamp >= since,
                UsageLedger.agent_id.is_not(None),
            )
            .group_by(UsageLedger.agent_id)
            .order_by(func.count(UsageLedger.id).desc())
            .limit(50)
        )
    ).all()

    return UsageOut(
        window_hours=hours,
        total_requests=int(total or 0),
        total_cost_usd=float(total_cost or 0.0),
        by_model=[
            UsageBucket(
                key=name,
                request_count=int(cnt),
                prompt_tokens=int(pt),
                completion_tokens=int(ct),
                cost_usd=float(cost or 0.0),
            )
            for (name, cnt, pt, ct, cost) in by_model_rows
        ],
        by_agent=[
            UsageBucket(
                key=str(agent_id),
                request_count=int(cnt),
                prompt_tokens=int(pt),
                completion_tokens=int(ct),
                cost_usd=float(cost or 0.0),
            )
            for (agent_id, cnt, pt, ct, cost) in by_agent_rows
        ],
    )


def _parse_window(value: str) -> int:
    """Return window size in hours. Fallback: 24h."""
    value = (value or "").strip().lower()
    if not value:
        return 24
    try:
        if value.endswith("h"):
            n = int(value[:-1])
            return max(1, min(n, 24 * 30))
        if value.endswith("d"):
            n = int(value[:-1])
            return max(1, min(n * 24, 24 * 30))
        n = int(value)
        return max(1, min(n, 24 * 30))
    except ValueError:
        return 24
