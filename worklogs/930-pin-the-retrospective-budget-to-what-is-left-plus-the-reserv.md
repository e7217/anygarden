# fix(campaigns): pin the retrospective budget to what is left plus the reserve and document the soft limit (#930)

- Commit: `aad4df0` (aad4df0)
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: —

## Situation

The campaign retrospective cycle (Phase 6, CMP-10/CMP-14) runs after the loop ends and is allowed to spend what the cycle budget left plus the retro reserve. Before this fix the budget calculation for a retrospective execution was not clearly pinned: if cycles had consumed more than the usable quota (i.e. they overshot into the reserve), the retrospective received a larger cap than what actually remained of the full budget. The documentation in `docs/campaigns.md` also described the rule loosely, leaving the soft-limit semantics implicit.

## Task

- Fix `cycle_execution_limits` in `service.py` to compute the retrospective's `max_total_tokens` as `budget - tokens_used` (full budget minus all spending, floored at 0), so that:
  - When cycles ended early the retrospective gets the unspent usable tokens **plus** the reserve.
  - When cycles overshot the usable quota, only the remaining reserve fraction is available.
- Add a parametrized test asserting both cases (`tokens_used=50_000` → cap 50_000; `tokens_used=97_000` → cap 3_000).
- Update `docs/campaigns.md` to document the soft-limit nature of the token cap (the engine settles usage after the call ends, so a single long turn may overshoot).

## Action

**`packages/cluster/anygarden/campaigns/service.py` (`cycle_execution_limits`, ~line 517–533)**

- Replaced the previous retrospective token computation (which used `remaining_cycle_tokens` or a partial formula) with:
  ```python
  remaining = max(0, int(campaign.budget["max_total_tokens"]) - int(campaign.tokens_used or 0))
  ```
  This covers both the "early stop" case (cycles left tokens untouched → more headroom) and the "overshoot" case (cycles consumed into the reserve → only what is left of the reserve remains).
- Guarded with `if remaining <= 0: raise CAMPAIGN_BUDGET_EXHAUSTED` so a fully-spent budget is rejected before an execution is opened.
- The docstring on `cycle_execution_limits` was updated to name the formula explicitly: `budget - tokens_used` for retrospectives, `remaining_cycle_tokens` for normal cycles.

**`packages/cluster/tests/test_campaigns_retrospective.py`**

- Added `test_a_retrospective_gets_what_is_left_plus_the_reserve` (parametrized):
  - `tokens_used=50_000, cap=50_000` — 45 k usable left + 5 k reserve = 50 k.
  - `tokens_used=97_000, cap=3_000` — cycles overshot usable 95 k; only 3 k of the 5 k reserve remains.
- Kept the existing `test_a_retrospective_runs_on_the_reserve_after_the_deadline` (95 k used → cap 5 k) as the canonical single case.

**`docs/campaigns.md`**

- Revised the retrospective-budget paragraph to state that the cap is `budget − 사용량` (budget minus usage), that cycles may overshoot into the reserve if a single turn settles late, and that after that only what is left of the reserve is available.

## Decisions

**Why `budget - tokens_used` instead of a two-part formula (usable + reserve separately)?**

The plan (§3.5 and §3.6) describes the reserve as a fraction of the full budget set aside for retrospectives, but the cycle quota is itself `usable = budget × (1 - reserve_ratio)`. Tracking usable and reserve independently would require storing or recomputing both. The single expression `budget - tokens_used` achieves the same invariant: while usage stays under the usable quota the retrospective sees `usable_left + full_reserve`; once usage exceeds the usable quota it sees only `full_budget - tokens_used`, which is strictly the reserve remainder. No extra columns needed.

**Why not raise an error when cycles have already consumed the reserve?**
The hard error fires only when `remaining <= 0` (nothing left at all). A partial reserve is still useful headroom; silently clamping to whatever is left is the right behaviour — the retrospective just runs shorter. The test at `tokens_used=97_000` verifies this path.

**Documentation choice**: `docs/campaigns.md` targets non-technical users. The soft-limit caveat (a long turn can overshoot) was already present for cycle executions in the same file; mirroring it for retrospectives keeps the model consistent and avoids user surprise when `tokens_used` slightly exceeds the budget.

## Result

- `test_a_retrospective_gets_what_is_left_plus_the_reserve` (two parametrized cases) passes.
- `test_a_retrospective_runs_on_the_reserve_after_the_deadline` continues to pass (95 k used → 5 k cap unchanged).
- `test_budget_end_runs_a_retrospective` (3 × 40 k cycles on a 100 k budget → exhausted) continues to pass.
- Ruff checks pass on all three modified files.
- `docs/campaigns.md` now documents the `budget − 사용량` formula and the soft-limit overshoot caveat for retrospectives.
