"""RFC #652 acceptance — cluster-side budget/usage baseline (task #94).

Captures the CURRENT budget-history semantics at main 6a2d217 so the
usage-ledger separation (#92) can be verified to preserve them. This is a
SCHEMA fingerprint: the columns of the usage stream and the budget policy
table are what #92's neutral-ledger move must keep identical (data rows need
FK-valid parents and are covered by the existing gateway regression suite).
"""
from __future__ import annotations

from anygarden.db.models import LLMGatewayUsage, TokenBudgetPolicy

USAGE_COLS = [
    "id", "timestamp", "agent_id", "room_id", "identity_kind", "identity_id",
    "model_name", "prompt_tokens", "completion_tokens", "cost_usd",
    "duration_ms", "status_code", "error",
]
POLICY_COLS = [
    "id", "scope_type", "scope_id", "token_ceiling", "warn_percent",
    "window_kind", "hard_stop_enabled", "is_active", "created_at",
    "updated_at",
]


def test_budget_usage_stream_schema_baseline():
    cols = [c.name for c in LLMGatewayUsage.__table__.columns]
    assert cols == USAGE_COLS, cols


def test_budget_policy_schema_baseline():
    cols = [c.name for c in TokenBudgetPolicy.__table__.columns]
    assert cols == POLICY_COLS, cols
