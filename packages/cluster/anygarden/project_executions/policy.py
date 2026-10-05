"""Capabilities enforced by the managed project workflow, before dispatch.

These declarations cover server operations. They do not sandbox native shells
or external MCP servers, and therefore cannot promise arbitrary egress limits.
"""

from __future__ import annotations

INTERNAL_TASK_ROLES = frozenset(
    {"work", "qa", "repair", "planning", "research", "implementation", "release"}
)
SUPPORTED_ACTIONS = INTERNAL_TASK_ROLES | frozenset(
    {
        "internal_work",
        "publish_artifact",
        "request_input",
        "managed_submission",
        "managed_deployment",
    }
)
SUPPORTED_LIMITS = frozenset(
    {"max_delegations", "max_depth", "deadline_at", "max_repair_rounds",
     "max_native_invocations", "max_total_tokens"}
)


def repair_round_limit(limits: dict | None) -> int:
    """Require an explicit nonnegative round budget; omitted means no repairs."""
    value = (limits or {}).get("max_repair_rounds", 0)
    if type(value) is not int or value < 0:
        raise ValueError("max_repair_rounds must be a nonnegative integer")
    return value


def unsupported_policy(
    allowed_actions: list | None, limits: dict | None
) -> tuple[list[str], list[str]]:
    """Return unknown declarations without interpreting natural language grants."""
    actions = [
        str(action)
        for action in allowed_actions or []
        if not isinstance(action, str) or action not in SUPPORTED_ACTIONS
    ]
    limit_names = [str(key) for key in limits or {} if key not in SUPPORTED_LIMITS]
    return actions, limit_names


def allows_operation(allowed_actions: list | None, operation: str) -> bool:
    """Empty policy permits internal work; managed external actions need a grant."""
    declared = set(allowed_actions or [])
    if operation in {"managed_submission", "managed_deployment"}:
        return operation in declared
    if not declared or "internal_work" in declared:
        return True
    return operation in declared
