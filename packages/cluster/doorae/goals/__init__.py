"""Autonomous responsibility (Goal) subsystem (#302 Phase 2).

Goal = recurring duty definition (recurrence + spec template).
Task = single execution unit (manual + scheduler-fired both live in
``tasks`` table; ``goal_id`` distinguishes scheduled rows).

Module layout:
- ``policy``    : pure functions (cron validation, next_run_at,
                  materialize_decision, failure threshold)
- ``executor``  : trigger → Task creation + assignment-message inject
- ``scheduler`` : background polling loop integrated with FastAPI
                  lifespan
- ``reporter``  : optional silent-success status pings (placeholder
                  in MVP)

The scheduler is in-process and assumes a single cluster replica;
multi-replica HA lands in #302 Phase 3 with PostgreSQL advisory locks.
"""

from doorae.goals.policy import (
    GOAL_FAILURE_PAUSE_THRESHOLD,
    MIN_INTERVAL_SECONDS,
    InvalidTriggerConfig,
    MaterializeDecision,
    compute_next_run_at,
    materialize_decision,
    validate_trigger_config,
)

__all__ = [
    "GOAL_FAILURE_PAUSE_THRESHOLD",
    "InvalidTriggerConfig",
    "MIN_INTERVAL_SECONDS",
    "MaterializeDecision",
    "compute_next_run_at",
    "materialize_decision",
    "validate_trigger_config",
]
