# fix(campaigns): refuse wake-ups and block reports on a cycle closed meanwhile, and huge or non-finite delays (#930)

- Commit: `1931f85` (1931f859a9b1d4f1cdc9d07d389e35ff006e601c)
- Author: Changyong Um
- Date: 2026-10-26T16:03:51+09:00
- PR: #930

## Situation

The campaign cycle agent tools `schedule_wakeup` and `report_blocked` each issued a blind `UPDATE` that assumed the cycle was still open. If a concurrent writer (e.g. a lifecycle sweep or a forced-close) closed the cycle between the guard-read and the write, the update silently matched zero rows and the agent's changes were lost — the TOCTOU window was undetected. Separately, `schedule_wakeup` accepted any numeric `delay_seconds`, including huge finite values that overflowed `timedelta` and non-finite IEEE values (`inf`, `-inf`, `nan`) that bypassed the range check.

## Task

- Detect the TOCTOU race in `schedule_wakeup` and `report_blocked`: when the `UPDATE … WHERE closed_at IS NULL` matches zero rows, raise a dedicated `CAMPAIGN_CYCLE_CLOSED` error so the agent turn is aborted cleanly.
- Reject non-finite `delay_seconds` with `CAMPAIGN_INVALID_ARGUMENT` before any arithmetic.
- Reject huge finite `delay_seconds` values (outside `[MIN_WAKEUP_SECONDS, MAX_WAKEUP_SECONDS]`) with `CAMPAIGN_WAKEUP_OUT_OF_RANGE` without crashing on `timedelta` overflow.
- Add tests covering all three new error paths.

## Action

**`packages/cluster/anygarden/campaigns/agent_actions.py`**

- Added `import math` (line 19).
- Extracted helper `_closed_meanwhile()` (lines 313–317) returning a `CampaignToolError("CAMPAIGN_CYCLE_CLOSED", …)` to deduplicate the error construction.
- In `schedule_wakeup` (lines 354–393):
  - Added `math.isfinite(delay)` guard — raises `CAMPAIGN_INVALID_ARGUMENT` for `inf`/`-inf`/`nan`.
  - Computed `wake_at` only when `delay` is within range (avoids `timedelta` overflow on huge values); otherwise sets `wake_at = None` and the existing range check catches it.
  - Captured `result` from `db.execute(update(…))` and checks `result.rowcount != 1` → raises `_closed_meanwhile()`.
- In `report_blocked` (lines 425–440):
  - Same `rowcount` guard added after the `UPDATE`.

**`packages/cluster/tests/test_campaigns_tools.py`**

- `test_a_huge_wakeup_is_refused_not_a_crash` — parametrised over `1e20` and `-1e300`; expects `CAMPAIGN_WAKEUP_OUT_OF_RANGE`.
- `test_a_non_finite_wakeup_is_an_invalid_argument` — parametrised over `inf`, `-inf`, `nan`; drops to the `agent_actions` layer directly; expects `CAMPAIGN_INVALID_ARGUMENT`.
- `test_a_cycle_closed_meanwhile_refuses_wakeup_and_block_reports` — parametrised over `schedule_wakeup` / `report_blocked`; opens a cycle, closes the `CampaignAttempt` row directly via a second `UPDATE`, then calls the tool; expects `CAMPAIGN_CYCLE_CLOSED` and verifies the in-memory `attempt` object was not mutated.

## Decisions

- **Capture `rowcount` vs. re-querying**: checking `result.rowcount` after a conditional `UPDATE WHERE closed_at IS NULL` is the lightest way to detect the race — no extra SELECT, no pessimistic locking. The alternative (SELECT + lock + UPDATE) would be heavier and still subject to races under some isolation levels.
- **Overflow guard before `timedelta`**: building `timedelta(seconds=delay)` with a huge-but-finite float raises `OverflowError` at the C level rather than a clean domain error. Clamping the path to `None` when the delay is out of range, and then letting the existing range check fire, avoids any exception-type leak into the API surface.
- **Non-finite check first**: `math.isfinite` is checked before the range comparison because `float("nan") < MAX_WAKEUP_SECONDS` is `False` in Python (NaN comparisons are always false), so the range check alone would pass and the `timedelta` call would raise a `ValueError`. Separating concerns makes the error code unambiguous.
- **`_closed_meanwhile` helper**: the identical error was needed in two places; a one-liner factory eliminates the copy-paste and gives the error a searchable name.

## Result

- Agents that attempt to schedule a wake-up or report a block on a cycle that was concurrently closed now receive `CAMPAIGN_CYCLE_CLOSED` and are instructed to end the turn, rather than silently losing the update.
- `delay_seconds` values of `inf`, `-inf`, or `nan` are now refused immediately with `CAMPAIGN_INVALID_ARGUMENT`.
- Huge-but-finite delay values (e.g. `1e20`) no longer crash the process; they are refused with `CAMPAIGN_WAKEUP_OUT_OF_RANGE`.
- Three new parametrised test cases cover all added guard branches.
