# chore(tests): remove codex-driven E2E scripts that bypass the agent runtime (#725)

- Commit: `812387a` (812387ad1e4572041250819261b5c933e816c5a9)
- Author: Changyong Um
- Date: 2026-09-28T14:43:28+09:00
- PR: #725

## Situation

`packages/cluster` carried three "real LLM E2E" scripts (`scripts/e2e_full_pipeline.py`, `scripts/e2e_multiprocess.py`, `scripts/e2e_real_chat.py`) and one `@pytest.mark.slow` test (`tests/test_e2e_real_conversation.py`). All four produced the "agent" reply by calling `codex exec` from the test itself and posting it with an agent token, so the product's agent runtime, turn delivery and engine adapters were never executed. They had not been meaningfully touched since the 2026-04-14 monorepo bootstrap, pointed at a nonexistent `anygarden-server` directory, never ran in CI, and `e2e_multiprocess.py` crashed immediately because `MachineDaemon` no longer accepts `max_agents`.

## Task

- Delete the four files without losing coverage the product still needs.
- Remove everything that pointed at them: the `make e2e` target, the `slow` marker/`addopts`, and a docstring reference.
- Keep `make e2e-setup` (Playwright browser install) and the Engine Smoke scripts intact.

## Action

- Deleted `packages/cluster/scripts/e2e_full_pipeline.py`, `e2e_multiprocess.py`, `e2e_real_chat.py`, and `packages/cluster/tests/test_e2e_real_conversation.py` (−1,037 lines).
- `packages/cluster/Makefile`: dropped `e2e` from `.PHONY` and removed the `e2e:` target.
- `packages/cluster/pyproject.toml`: removed the `slow` marker definition, its comment, and `addopts = "-m 'not slow'"` (the deleted test was the only user).
- `packages/cluster/tests/test_e2e_materialize.py:15`: removed the sentence pointing to `scripts/e2e_multiprocess.py`.

## Decisions

- Options weighed:
  - **Delete** — chosen.
  - **Repair** (drop `max_agents`, fix usage comments) — rejected: the test would still generate the agent's reply itself, so the product path stays untested while LLM cost and nondeterminism remain.
  - **Rewrite as a true product-path E2E** — rejected as out of scope: real-engine verification is already owned by Engine Smoke (`.github/workflows/engine-smoke.yml`, `docs/runbook/release-gate.md`), which runs under an approved, budget-capped, egress-isolated environment. A local script would duplicate it without those controls.
- Deciding observation: every file posts `call_codex()` output as the agent, i.e. the "agent" is the test script. Repairing cannot change that.
- Coverage check before deleting: the remaining verifiable behavior (WS send/echo, persistence, since_seq replay) is covered deterministically by `tests/test_ws_handler.py` (`test_since_seq_replays_missed_messages`, `test_since_seq_replays_more_than_one_page`) and `tests/test_e2e_scenario.py`.
- `slow` marker removed rather than kept for future use: zero users after deletion, its description names retired engines (claude, gemini), and re-adding it is a one-liner.
- `docs/history/STATUS.md` still mentions the deleted test; left unchanged because it is a dated historical snapshot.
- Revisit if: someone relied on `make -C packages/cluster e2e` locally, or a future gap in since_seq/reconnect coverage appears — add an LLM-free test rather than restoring these scripts.

## Result

- 7 files changed, +2 / −1,050.
- `uv run pytest` in `packages/cluster`: 1972 passed (collection unchanged at 1972; the previous "1 deselected" is gone).
- `make -C packages/cluster help` lists only `e2e-setup` for e2e.
- ruff: no new findings (pre-existing baseline 1,400 → 1,398 on this branch, the delta being the deleted file).
- Out of scope, left for follow-up: unit-test redundancy audit; root `make test` (`uv run pytest packages/`) vs. per-package CI execution mismatch.
