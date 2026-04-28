# fix(migrations): renumber 038_task_assigned_at → 039 to unbreak main

- Commit: `a35f2e4` (a35f2e4...)
- Author: Changyong Um
- Date: 2026-04-28
- PR: TBD (follow-up to #317)

## Situation

PR #317 (#314 — task assignment broadcast + sweeper) and PR #316 (#309 — agent permission level) were prepared and merged in close succession. Both introduced migration files numbered `038_*` with `revision="038" down_revision="037"`. After both landed, `main` had two distinct revision-038 heads, so `alembic.script_directory.get_current_head()` raised `MultipleHeads` on every cold-DB bootstrap.

CI Linux caught it (`test_mcp_secrets_persistence` was the first test to hit `_ensure_schema_ready`); macOS Windows happened to pass earlier in the run before CI's caching exposed the conflict consistently. Local pre-merge runs of either branch in isolation passed because each branch saw only its own 038.

## Task

- Restore `main` to a single linear revision chain.
- Pick which of the two 038s gets renumbered. The agent_permission_level one had merged a few minutes earlier, so renumbering #314's file is the smaller diff and avoids touching unrelated test expectations.
- Keep the actual schema change (`tasks.assigned_at` column + backfill) byte-identical so reviewers don't have to re-read it.
- Bump test expectations that hardcode the head revision string.

## Action

- `git mv packages/cluster/doorae/db/migrations/versions/038_task_assigned_at.py 039_task_assigned_at.py`
- Inside the file: `revision = "039"`, `down_revision = "038"`. Added a paragraph at the top explaining why the file was renumbered so a future maintainer doesn't re-hit the same race.
- `tests/test_migrations.py`: `sed 's/== "038"/== "039"/g; s/head == "038"/head == "039"/g'` — five call sites in `test_upgrade_head_on_fresh_db`, `test_fresh_db_creates_and_stamps`, `test_already_stamped_db_runs_upgrade`, `test_legacy_unstamped_db_refuses_to_boot`, and `test_038_backfills_assigned_at_for_assigned_tasks`'s scope (the test name itself stayed `test_038_*` — renaming it would churn the test ID without semantic value; the docstring still describes the behavior).

## Decisions

- **Rename #314's migration vs. #309's** — picked #314 because (a) it landed second so it's the natural "rebase on top" target, (b) the diff stays inside one PR and one author, and (c) the test-expectations changes are smaller (#314's tests were already in this branch; #309's tests would have needed coordination with the other PR's author).
- **Pure renumber vs. squash both 038s into one** — squashing would have been cleaner historically but requires force-push or a revert+reland of one of the two PRs. Renumber is reversible, additive, and ships in minutes.
- **Assumption to revisit if violated**: this is a one-off race symptom. If two PRs claim the same migration number again we should add a CI check ("does the alembic graph have a single head?") rather than handling it manually. Tracked as a possible follow-up.

## Result

- Single linear chain restored: `037 → 038_agent_permission_level → 039_task_assigned_at`.
- Local `uv run pytest -q` (cluster) → 908 passed, 1 deselected.
- Test (Linux) failure on #317 should not recur on this PR's CI.
