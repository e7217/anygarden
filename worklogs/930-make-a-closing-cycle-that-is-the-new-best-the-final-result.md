# fix(campaigns): make a closing cycle that is the new best the final result (#930)

- Commit: `74d7e78` (74d7e789dffeab92d3e634082cf2f4a98389c0cb)
- Author: Changyong Um
- Date: 2026-10-26T16:02:10+09:00
- PR: #930

## Situation

When a campaign loop ends, `apply_transition` in `service.py` computes the final attempt (`final_attempt_id`) by calling `default_final_attempt_id`, which reads `campaign.best_attempt_id` from the database object. However, the same `apply_transition` call can also update `best_attempt_id` in the same write (e.g., a closing cycle that turns out to be the best). Because the DB object has not yet been flushed at the point of the call, `campaign.best_attempt_id` still held the old (pre-transition) value, causing the newly-best cycle to be ignored as the final result.

## Task

- Ensure that when a closing transition also promotes a new best attempt, `default_final_attempt_id` uses the in-flight `best_attempt_id` rather than the stale ORM value.
- Keep the existing fallback behaviour intact for transitions that do not change `best_attempt_id`.
- No schema changes; no migration needed.

## Action

- `packages/cluster/anygarden/campaigns/service.py` (`apply_transition`, line ~249):
  - When constructing `values["final_attempt_id"]`, pass `best_attempt_id=values.get("best_attempt_id", campaign.best_attempt_id)` so the in-flight value wins if present.
- `packages/cluster/anygarden/campaigns/service.py` (`default_final_attempt_id`, line ~275):
  - Added keyword-only parameter `best_attempt_id: str | None = None`.
  - Updated docstring to document the override semantics.
  - Changed the final fallback from `campaign.best_attempt_id` to `best_attempt_id if best_attempt_id is not None else campaign.best_attempt_id`.

## Decisions

- **Override via parameter vs. re-query after flush**: passing the pending value as a parameter avoids an extra DB round-trip and keeps the function pure/deterministic; a re-query would require flushing first (side-effectful) or accepting a stale read.
- **Keyword-only (`*`)**: makes the override opt-in and prevents accidental positional misuse at existing call sites.
- **`None` sentinel**: `None` is already the natural "no best" value for `campaign.best_attempt_id`, so a distinct sentinel was not needed; the guard `if best_attempt_id is not None` preserves the original fallback when the transition does not touch `best_attempt_id`.
- Assumption: callers always set `values["best_attempt_id"]` when they intend to update it in the same transition; if a future caller omits it while changing best_attempt, this fix would silently revert to stale behaviour.

## Result

- A campaign that closes with a cycle that is simultaneously the new best will now correctly record that cycle as `final_attempt_id`.
- Existing behaviour for campaigns whose best attempt was set earlier is unchanged (fallback path preserved).
- No new tests were added in this commit; coverage relies on the adjacent commit's test suite (`test_campaigns_cycles.py`) which exercises closing-cycle scenarios.
