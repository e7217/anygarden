# fix(campaigns): measure real-clock stamps against the real clock in cycle tests (#999)

- Commit: `0015541` (0015541c8ecd78e0da01aec1ed5e42c35483d822)
- Author: Changyong Um
- Date: 2026-11-09T14:51:57+09:00
- PR: #999

## Situation

The test module sets `T0` once at import time. The terminal hook and sweeper hook stamp eval deadlines and cycle-closing timestamps against the real system clock, not `T0`. Under `pytest-xdist` the full Linux suite can take upward of six minutes, so a late-running test could observe the real clock more than five minutes past `T0`. Two assertions compared `attempt.eval_deadline_at - T0` against fixed windows, causing them to fail on every run once the suite grew past that threshold.

## Task

- Fix `test_completed_run_requests_an_evaluation_with_a_persisted_job` and `test_a_pending_eval_has_an_outer_deadline` so their eval-deadline assertions are immune to wall-clock drift relative to `T0`.
- Fix `test_s2b_unpicked_cycle_is_swept_and_campaign_moves_on` so it ticks the campaign at the correct real-clock moment, not at a hardcoded `T0 + 12 min` offset.
- Keep assertions tight (5-minute windows) to still catch regressions.

## Action

**`packages/cluster/tests/test_campaigns_cycles.py`**

- `test_completed_run_requests_an_evaluation_with_a_persisted_job` (line ~561):
  - Capture `before = datetime.now(UTC)` immediately before `_completed_cycle` and `after = datetime.now(UTC)` immediately after.
  - Replace `timedelta(hours=24) < attempt.eval_deadline_at - T0 < timedelta(hours=24, minutes=5)` with `before + timedelta(hours=24) < deadline < after + timedelta(hours=24, minutes=5)`, anchoring to the real clock bracket rather than `T0`.

- `test_a_pending_eval_has_an_outer_deadline` (line ~925):
  - Same `before`/`after` bracket around `_completed_cycle`.
  - Replace `attempt.eval_deadline_at - T0` comparison with `before + timedelta(hours=24) < attempt.eval_deadline_at < after + timedelta(hours=24, minutes=5)`.

- `test_s2b_unpicked_cycle_is_swept_and_campaign_moves_on` (line ~344):
  - Read `next_cycle_at` from the swept campaign instead of assuming `T0 + 12 min` is always in the future.
  - Tick at `max(T0 + timedelta(minutes=12), next_cycle_at)` so the tick fires when the campaign itself says its next cycle is due, regardless of how far the real clock has drifted past `T0`.

## Decisions

- **Bracket with `before`/`after` instead of freezing the clock**: Freezing the system clock (e.g. via `freezegun` or monkeypatching `datetime.now`) would require changes to production code or invasive patching of internal hook machinery. Reading `before`/`after` around the call is a zero-production-impact, one-line change per test that keeps the window semantics intact.
- **`max(T0 + 12min, next_cycle_at)` for the sweep tick**: The sweep hook writes `next_cycle_at` on the real clock. Using the campaign's own value as the lower bound ensures the tick is never behind the value the system stored, without hardcoding an arbitrary safety margin.
- **Window kept at 5 minutes**: A wider window (e.g. 1 hour) would make the test vacuous. Five minutes is generous for CI but tight enough to catch a misconfigured deadline multiplier.
- **Assumption to revisit**: If `_completed_cycle` itself is ever made to inject a frozen clock, the `before`/`after` bracket would need to move inside it.

## Result

The three affected tests now measure eval deadlines and sweep ticks against the real clock rather than a fixed `T0`, making the suite pass regardless of how long earlier tests take. The module passes with `T0` shifted from −1 to +30 minutes off the real clock, confirming the fix covers the full observed CI drift range.
