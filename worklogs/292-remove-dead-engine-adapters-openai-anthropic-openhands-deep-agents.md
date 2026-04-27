# chore(agents): remove dead engine adapters (openai, anthropic, openhands, deep-agents) (#292)

- Commit: `c013e5a` (c013e5ad398cb8ced776b8d6c14ef244ff229500)
- Author: Changyong Um
- Date: 2026-04-28T00:58:19+09:00
- PR: #292

## Situation

`packages/agent/doorae_agent/integrations/` carried four adapters
(`openai.py`, `anthropic.py`, `openhands.py`, `deep_agents.py`) that
were registered in the `ENGINES` lazy-loader, advertised in the
`ENGINE_CATALOG` admin UI listing, and detectable by `doorae-machine`
on its host. They had been there since the MVP scaffolding phase but
never received any of the protocol features the CLI adapters
accumulated through #157, #190, #237, #255, #279, #283, #284, #286,
#289 — no `RoomHandlerSupervisor` wiring (so no turn timeout, no
self-echo cycle guard, no per-engine metrics), no `<room_conversation>`
context wrap, no roster injection, no shared-context memory. Selecting
one in the admin UI silently produced a degraded session: messages
went out but the multi-agent room conventions did not apply.

## Task

- Confirm the adapters are unreachable from production paths (no
  `RoomHandlerSupervisor.engine_name` references, no manifest fixtures
  using them, only graceful-fallback tests).
- Remove the adapter modules, their tests, the registry entries, the
  catalog entries, the CLI dispatch branches, the host detector
  entries, and the optional-dependency declarations.
- Reconcile every test that used the dead engine names as placeholder
  strings for "non-MCP / non-bridge engine" so the parametrize lists
  and synthetic engine values stay coherent with the new closed set
  of three.
- Provide a forward-only DB migration that redirects any pre-existing
  `agents.engine` row pinned to one of the dead values to
  `claude-code`, so an upgrade does not start raising
  `ValueError("Unknown engine ...")` on spawn.

## Action

- Deleted modules: `packages/agent/doorae_agent/integrations/{openai,anthropic,openhands,deep_agents}.py` and the corresponding `packages/agent/tests/test_integrations/test_*.py` files (graceful-fallback tests only — no integration coverage to preserve).
- Pruned the `ENGINES` and `_ADAPTER_CLASSES` dicts in `packages/agent/doorae_agent/integrations/__init__.py:17-36` and updated the module docstring to reflect the closed set (claude-code / codex / gemini-cli).
- Removed the four `engine == "..."` branches in `packages/agent/doorae_agent/cli.py:_setup_engine`.
- Dropped the `openai` and `anthropic` `EngineCatalogEntry` blocks in `packages/cluster/doorae/engines/catalog.py:181-206` and adjusted the module docstring's `gpt-5.5` exception note to no longer reference openai.
- `packages/cluster/tests/test_engine_catalog.py:32-33` — removed the asserts for the dropped catalog keys.
- `packages/machine/doorae_machine/detector.py` — collapsed the file from a 6-detector implementation (3 binaries, 1 Python import, 2 env-var) to the 3-binary set that maps onto the surviving engines. Deleted `BINARY_ENGINES`'s `openhands` row, the `PYTHON_ENGINES` deepagents row, the `ENV_ENGINES` table entirely, the unused `os` import, and the corresponding test classes in `packages/machine/tests/test_detector.py`.
- `packages/machine/doorae_machine/agent_dir.py:36-42` — removed `.openhands/` from `_ALLOWED_PREFIXES`; updated `packages/machine/tests/test_agent_dir.py:31` to drop the matching valid-path case.
- `packages/machine/doorae_machine/spawner.py:626-630` — rewrote the comment about the workspace shared-files bridge to describe the membership check as defensive code for future engines instead of listing now-removed raw-SDK names.
- `packages/machine/tests/test_spawner.py:478-495` — pruned `openhands` from the parametrize list and dropped the `if engine != "openhands"` branch in the manifest fixture.
- `packages/machine/tests/test_materialize.py` — three parametrize lists (lines 627, 686, 822) lost their `openhands` entry; the `TestWorkspaceSharedBridge` class lost `test_raw_sdk_engine_has_no_bridge` and `test_stale_bridge_from_previous_engine_is_replaced` because the only engines left all want the bridge, and rewrote the class docstring accordingly.
- `packages/cluster/doorae/mcp_templates/merge.py:49-55` — `settings_path_for_engine`'s docstring now talks about "echo or any unknown engine" instead of naming `openai`/`anthropic`.
- `packages/agent/doorae_agent/profile/schema.py:14` — comment example updated to claude-code/codex/gemini-cli.
- `packages/agent/examples/profiles/{coder,host}.yaml` — both example profiles repointed to `engine: claude-code` (Coder kept its empty model, Host moved from `gpt-4o` to `claude-sonnet-4-6`).
- `packages/agent/tests/test_cli.py:42-56` — `TestProfileLoading.test_load_example_profile` now writes `engine: claude-code` so the YAML round-trip case stays meaningful after the schema example was updated.
- `packages/cluster/tests/test_e2e_scenario.py:66` — fixture Agent row repointed from `engine="openai"` to `engine="claude-code"`.
- `packages/agent/pyproject.toml` — dropped `openai>=1.30` and `anthropic>=0.25` from the default `dependencies` list (unused after adapter removal; transitively available via `codex-python` and `claude-agent-sdk` if anything still needs them) and pruned `openai`/`anthropic`/`openhands`/`deep-agents` from `[project.optional-dependencies]`.
- New migration `packages/cluster/doorae/db/migrations/versions/035_drop_dead_engine_values.py` runs two `op.execute` statements: `UPDATE agents SET engine='claude-code' WHERE engine IN ('openai','anthropic','openhands','deep-agents')` and the same `WHERE` filter as a `DELETE FROM machine_engines`. Downgrade is intentionally a no-op — restoring the strings would require restoring deleted adapter modules.
- `packages/cluster/tests/test_migrations.py` — bumped four `assert version == "034"` and one `assert head == "034"` checkpoints to `"035"`.

## Decisions

Sources mined: `.tmp/plan-292-dead-adapter-cleanup.md` (the worktree-plan output for this issue), the conversation in `.tmp/plan-292-dead-adapter-cleanup.md` §3.2 capturing the alternatives.

- **Single PR vs four sequential PRs**: chosen single PR. The justification is identical for all four adapters (no supervisor wiring, no context plumbing, no production fixture), so splitting would require repeating the same review four times for a delete-dominant diff. The plan §3.2 decision A captured this trade-off.
- **Migration-with-no-op vs no migration**: chosen migration. Production row count is presumed zero, but the migration is ~30 lines and the failure mode of skipping it ("ValueError: Unknown engine 'openai'" at spawn time) is silent enough — and rare enough — that we'd diagnose it slowly. Cheaper to ship the safety net. Plan §3.2 decision B.
- **Detector openhands binary**: chosen "remove" over "keep for future re-add". The detector's only consumer was the now-deleted adapter dispatch path; keeping a detection result with no consumer is YAGNI. If OpenHands ever returns to doorae it will likely arrive as an ACP peer (a separate subsystem), not as a binary the host probes. Plan §3.2 decision C.
- **Raw-SDK negative tests in `test_materialize.py`**: chosen to delete rather than synthesise a placeholder engine name. The spawner's `if msg.engine in ("codex", "claude-code", "gemini-cli")` membership check stays as defensive code, but writing tests with a fake engine like `"raw-test"` would test our fixture rather than a real branch. Future re-add of a non-bridge engine would naturally come with its own tests.
- **`openai`/`anthropic` strings in `test_mcp_templates_*` left intact**: those tests use the names as representative "unknown engine" strings to assert the negative branch of `merge_for_engine` (raises `ValueError`) and `settings_path_for_engine` (returns `None`). The functions don't enumerate the dead adapter set — they hardcode the supported set — so the tests remain semantically correct even after the names are no longer engine identifiers. Marked as a follow-up clean-up rather than a blocker.
- **Frontend untouched**: the admin UI's engine dropdown reads from `ENGINE_CATALOG` over the API at runtime, so removing the catalog entries collapses the dropdown automatically. No frontend code changes required, only a `npm run build` smoke check.

Assumption to revisit if violated: the migration's row redirect assumes `claude-code` is a usable default for any agent currently pinned to a dead engine. If future deployments introduce admins who configure `engine="openai"` in production via direct DB write, an automatic redirect to `claude-code` would silently change their intent. The PR description called for a `SELECT COUNT(*)` audit before merging — the migration is the safety net, not a replacement for that audit.

## Result

- 296 agent-package tests, 814 cluster-package tests, and 303 machine-package tests all pass after the cut.
- `uv run ruff check packages/` reports 126 errors on the branch vs 129 on main (no regressions; the three-error reduction comes from removed unused imports in deleted files).
- `alembic upgrade head` → `downgrade -1` → `upgrade head` round-trip is clean on a fresh SQLite database.
- `cd packages/cluster/frontend && npm run build` produces a clean bundle (the unrelated missing `anser` module had to be restored via `npm install` first; that fix-up rode along in the same dev environment but did not modify any tracked file).
- Net diff: 19 files modified, 8 files deleted, 1 file added; -283 line / +42 line at `--stat` level (with the deleted adapter and test bodies counted in the deleted-file inventory rather than diff stats).
- The four catalog entries are gone from the admin UI, so the dropdown in `AdminMachines.tsx` now shows three engines: `claude-code`, `codex`, `gemini-cli`.
- Pending: PR2 (#293) — centralizing memory/roster injection in `base.py` — is staged in `.tmp/plan-293-context-injection-base-centralization.md` and is the natural follow-up now that the adapter set is closed.
