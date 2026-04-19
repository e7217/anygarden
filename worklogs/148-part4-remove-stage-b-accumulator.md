# chore(agent): remove Stage B accumulator, collapse to server-driven path (#148 Part 4)

- Commit: (pending — see PR)
- Author: Changyong Um
- Date: 2026-04-19
- PR: follow-up to Parts 1 (#149) + 2 (#150) + 3 (#151)

## Situation

Part 3 (#151) moved the ambient decision to the server: cluster stamps `metadata.ingest_only` on ambient broadcasts when the per-room flag is on, and opted-out agents (`agents.context_window_opt_out`) demote it to `SKIP` in `decide_policy`. With that live, Stage B's env-driven `ContextAccumulator` became dead weight — it duplicates the same INGEST_ONLY promotion but from the agent side and behind env vars that are no longer wired to the admin UI. Keeping both paths around is exactly the "두 경로 유지 비용" risk the plan's decision 5 warned about. Part 4 deletes Stage B.

## Task

- Delete `doorae_agent/coordination/accumulator.py` and its test file.
- Drop `_ambient_capture_enabled()` from `decide_policy`; rule 5 (addressable mention) collapses to SKIP, rule 7 (agent sender no mention) collapses to SKIP. Direct mention (rule 3) and `ingest_only` flag (rule 4) still win in that order.
- Strip the `DOORAE_CONTEXT_WINDOW_ENABLED` / `_SIZE` env-var bootstrap from `cli.py`.
- Remove `TestDecidePolicyStageB` — its scenarios are now covered by the server-side tests in `test_ws_handler.py::TestContextWindowBroadcast` (Part 3) plus `TestDecidePolicy::test_opt_out_*`.
- Update `packages/agent/README.md` so the ambient section describes the final server-driven shape and explicitly calls the env vars removed.
- Touch adapter docstrings / comments that still mentioned "Stage B accumulator" so future readers don't chase a ghost.

## Action

### Deletions
- `packages/agent/doorae_agent/coordination/accumulator.py` — entire file (120 lines). The companion `pending_context.py` stays; it's the per-adapter buffer that `ingest_context` writes to and is still needed for INGEST_ONLY.
- `packages/agent/tests/test_coordination/test_accumulator.py` — entire file.
- `packages/agent/tests/test_integrations/test_should_respond.py::TestDecidePolicyStageB` — entire class + the `_reset_accumulator` autouse fixture.

### `decide_policy` simplification (`integrations/base.py`)
- Removed `_ambient_capture_enabled()` helper.
- Rule 5 (addressable mention not for us): no Stage B branch — straight to `MessagePolicy.SKIP`.
- Rule 7 (agent sender, no mention): no Stage B branch — straight to `MessagePolicy.SKIP`.
- Rule 4 (ingest_only flag + opt-out) unchanged from Part 3.
- Comments rewritten to point forward at `ws/handler.py::_is_ambient_candidate` rather than back at the deleted module.

### CLI + adapter housekeeping
- `cli.py::_run_agent` — dropped the `get_accumulator()` import + the `context_window.configured` startup log that read env vars. Nothing left in the function references the accumulator.
- `integrations/claude_code.py::ingest_context` docstring — "Stage B accumulator" → server-driven stamping (`[취합 결과]` or `context_window_enabled` room broadcasts).
- `integrations/gemini_cli.py` + `integrations/codex.py` — same doc update next to the `decide_policy` call site.

### Documentation
- `packages/agent/README.md` ambient section — rewritten to describe the server-driven flow (Part 3) and the per-agent opt-out (Part 2). Explicitly marks `DOORAE_CONTEXT_WINDOW_ENABLED` / `_SIZE` as **removed** (not just deprecated) with a pointer to the Room/Agent settings UI.

## Decisions

### Remove, not deprecate
- A. Keep `get_accumulator()` as a no-op wrapper that always returns `enabled=False` so a stale env var logs a "has no effect" warning. → Rejected: the same behaviour is achieved by simply not reading the env, and the dead module is one more place someone else would try to "fix" in the future.
- B. Delete outright → **chosen**. Part 2's UI toggle + Part 3's server path fully replace it; there is no feature gap between Stage B and the server-driven flow. The only admin-facing contract was the env var, and the README now tells anyone looking for it where the replacement lives.

Rationale: per plan §3.2 decision 5, "죽은 코드는 빠르게 정리해야 오해·레거시 부담 감소." The three PRs preceding this one already had Stage B and the new path coexisting; now that Part 3 has been exercised and stable across the test suite, the cleanup pass closes the window.

### Test scope rewrite
- The deleted `TestDecidePolicyStageB` class had 6 cases that asserted "with DOORAE_CONTEXT_WINDOW_ENABLED=1, X promotes to INGEST_ONLY." With the env var gone, these tests can no longer be rewritten to exercise the same behaviour at the same layer — the promotion now happens server-side. The replacement test surface is already in place:
  - `cluster/tests/test_ws_handler.py::TestContextWindowBroadcast` (4 cases from Part 3) — asserts the server stamps the flag correctly.
  - `agent/tests/test_integrations/test_should_respond.py::TestDecidePolicy::test_ingest_only_flag_*` + `test_opt_out_*` (from Parts 1-3) — asserts the agent reacts correctly to the stamp.
- So the two layers are each independently covered; the deleted class would have been a third copy of the same assertion.

### `pending_context.py` retained
- It's the storage layer for `ingest_context`, used by `ClaudeCodeAdapter`/`GeminiCliAdapter`/`CodexAdapter` to stash INGEST_ONLY messages for the next active turn. It has no Stage B dependency — it's just a bounded per-room buffer with a formatter. Keeping it is correct.

## Result

- `doorae_agent` no longer reads any `DOORAE_CONTEXT_WINDOW_*` env variable. Operators who had Stage B turned on get a cleaner behavior after deploy: the room flag (Part 1) governs whether ambient messages come through, and the per-agent opt-out (Part 2) governs whether this specific agent consumes them. The intent their env vars expressed is now expressible (and finer-grained) through the UI.
- cluster pytest: 586 passed — no test in the cluster package referenced the agent-side accumulator, so no changes there.
- agent pytest: 150 passed (down from 173 in Part 3 because the 23 removed Stage B tests — the Part 3 opt-out tests kept the overall coverage on `decide_policy` intact).
- machine pytest: 232 passed — untouched.
- `packages/agent/README.md` ambient section now reflects the Parts 1-3 shape and calls the env vars removed. No other `DOORAE_CONTEXT_WINDOW_` references remain in the tree.
- Completes the #148 four-part roadmap: (1) room flag storage + UI, (2) agent opt-out storage + UI, (3) runtime wiring, (4) Stage B removal.
