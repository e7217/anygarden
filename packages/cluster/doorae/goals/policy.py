"""Pure-function policy module for the Goal subsystem (#302 Phase 2).

Everything here is deterministic and DB-free so it can be exhaustively
unit-tested in isolation. The scheduler / executor / API layers all
feed inputs into these helpers and act on the returned decisions —
the policy module is the canonical home for "what does the spec
require us to do here" answers.

Key knobs:
- ``MIN_INTERVAL_SECONDS = 60`` — rejects accidental ``* * * * *``
  (every minute) crons. Prevents toy mistakes from spawning a
  per-second LLM hit.
- ``GOAL_FAILURE_PAUSE_THRESHOLD = 3`` — flips an active goal to
  ``paused`` after N consecutive failures and posts a heads-up to
  the report room. The number is intentionally low; failure modes
  in production are usually structural (auth expired, endpoint
  changed) and retrying past 3 has diminishing returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

from croniter import CroniterBadCronError, croniter


# Tunables — dataclass-style module constants. Kept here (not in
# settings) because changing them mid-flight requires server restart
# to re-validate jobstore state anyway.
MIN_INTERVAL_SECONDS: int = 60
GOAL_FAILURE_PAUSE_THRESHOLD: int = 3

# #314 — task pickup / execution timeouts. Measured from
# ``Task.assigned_at`` (todo) and ``Task.started_at`` (in_progress).
# Picked conservatively: pickup of <30s is the normal case for a
# healthy agent receiving a fresh task-assignment frame, and
# executions almost always finish in well under 5 minutes. Crossing
# either bound is a strong "agent is wedged or never woke" signal,
# so the sweeper flips the row to ``failed`` and lets
# ``apply_completion`` feed the ``consecutive_failures`` counter.
TASK_PICKUP_TIMEOUT_SECONDS: int = 120
TASK_EXECUTION_TIMEOUT_SECONDS: int = 600


class InvalidTriggerConfig(ValueError):
    """Raised by ``validate_trigger_config`` when the API receives a
    malformed cron string, a sub-minute interval, or a missing key.
    Callers translate this to HTTP 422."""


def validate_trigger_config(
    trigger_type: str, config: dict
) -> None:
    """Validate the ``trigger_config`` jsonb against ``trigger_type``.

    Raises ``InvalidTriggerConfig`` on any shape mismatch — never
    returns a corrected value. The API translates the raised message
    into an HTTP 422 detail string so users see actionable feedback
    ("``cron`` must be a 5-field expression"; "``interval_seconds``
    must be at least 60").
    """
    if trigger_type == "cron":
        cron = config.get("cron")
        if not isinstance(cron, str) or not cron.strip():
            raise InvalidTriggerConfig(
                "cron trigger requires {'cron': '<expression>'}"
            )
        try:
            it = croniter(cron, datetime.now(timezone.utc))
        except (CroniterBadCronError, ValueError) as exc:
            raise InvalidTriggerConfig(
                f"invalid cron expression: {exc}"
            ) from exc
        # Reject crons that fire more often than MIN_INTERVAL_SECONDS.
        # Estimate the smallest gap by sampling two consecutive fire
        # times — handles ``*/30 * * * *`` (30m) → ``*/2 * * * *``
        # (2m) → ``* * * * *`` (1m, REJECTED).
        first = it.get_next(datetime)
        second = it.get_next(datetime)
        gap = (second - first).total_seconds()
        if gap < MIN_INTERVAL_SECONDS:
            raise InvalidTriggerConfig(
                f"cron interval {gap:.0f}s is below the "
                f"{MIN_INTERVAL_SECONDS}s minimum"
            )
        return

    if trigger_type == "interval":
        secs = config.get("interval_seconds")
        if not isinstance(secs, int) or secs < MIN_INTERVAL_SECONDS:
            raise InvalidTriggerConfig(
                f"interval trigger requires "
                f"{{'interval_seconds': >= {MIN_INTERVAL_SECONDS}}}"
            )
        return

    if trigger_type == "manual":
        # Manual triggers carry no scheduling config; any config keys
        # are simply ignored. We don't reject — leaves room for the
        # frontend to round-trip arbitrary metadata.
        return

    raise InvalidTriggerConfig(
        f"unknown trigger_type: {trigger_type!r}"
    )


def compute_next_run_at(
    trigger_type: str,
    config: dict,
    *,
    after: datetime,
) -> datetime | None:
    """Compute the next fire time strictly after ``after``.

    Returns ``None`` for ``manual`` triggers (no auto-fire). Assumes
    ``validate_trigger_config`` has already accepted the inputs —
    callers should not pass unvalidated config here. ``after`` must
    be timezone-aware UTC; mixing naive datetimes silently drifts.
    """
    if trigger_type == "cron":
        it = croniter(config["cron"], after)
        return it.get_next(datetime)

    if trigger_type == "interval":
        return after + timedelta(seconds=int(config["interval_seconds"]))

    if trigger_type == "manual":
        return None

    raise InvalidTriggerConfig(
        f"unknown trigger_type: {trigger_type!r}"
    )


class MaterializeDecision(str, Enum):
    """Outcome of ``materialize_decision`` — a tiny state machine.

    ``KEEP`` — Task row stays in the ledger (default for ``full``
    goals + any failure / interesting result).
    ``DELETE`` — Task row was created during execution but the
    completion was a silent success on an ``interesting_only`` goal.
    The executor removes the row so the silent goal does not pollute
    Tasks UI. (Phase 2: archival flag instead of hard delete.)
    """

    KEEP = "keep"
    DELETE = "delete"


def materialize_decision(
    *,
    materialize: str,
    final_status: Literal["done", "failed"],
    is_interesting: bool,
) -> MaterializeDecision:
    """Compute keep-vs-delete for a finished goal-derived Task.

    Inputs are the goal's ``materialize`` flag, the agent-reported
    final ``status`` (``done`` or ``failed``), and the
    ``is_interesting`` flag (set by either the failure auto-flag or
    a future ``mark_run_interesting`` MCP call).

    Logic:
    - ``materialize == 'full'``                            → KEEP
    - ``failed`` or ``is_interesting``                     → KEEP
    - ``done`` + ``not is_interesting`` + interesting_only → DELETE
    """
    if materialize == "full":
        return MaterializeDecision.KEEP
    if final_status == "failed":
        return MaterializeDecision.KEEP
    if is_interesting:
        return MaterializeDecision.KEEP
    return MaterializeDecision.DELETE


@dataclass(frozen=True)
class FailureCounterUpdate:
    """Result of ``apply_completion_to_failure_counter``.

    ``pause`` is true iff the counter just crossed the
    ``GOAL_FAILURE_PAUSE_THRESHOLD`` — callers use this to flip the
    goal's status and post the heads-up message.
    """

    new_count: int
    pause: bool


def apply_completion_to_failure_counter(
    *,
    current: int,
    final_status: Literal["done", "failed"],
) -> FailureCounterUpdate:
    """Pure: derive new ``consecutive_failures`` + pause-trigger flag.

    Resets to 0 on success; increments on failure; flags pause when
    crossing the threshold. The DB write is the caller's job.
    """
    if final_status == "done":
        return FailureCounterUpdate(new_count=0, pause=False)
    new_count = current + 1
    return FailureCounterUpdate(
        new_count=new_count,
        pause=new_count >= GOAL_FAILURE_PAUSE_THRESHOLD,
    )
