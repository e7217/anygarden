# chore(agents): remove agent-ts and the agent runtime selector (#746)

- Commit: `de664af` (de664afbdd60a6f1ccfb6dbdf0dd7b93cfec91de)
- Author: Changyong Um
- Date: 2026-09-30T10:51:30+09:00
- PR: —

## Situation

#73 added a TypeScript agent runtime (`packages/agent-ts`) next to the Python one, along with a `runtime` selector covering the DB column, API, sync frame and spawner. #679 then moved all engine execution to the Python runtime (codex-cli, pi-cli). After that, `agent-ts` had no engine it could run. Its CLI rejected every engine, and the server rejected codex/pi with any non-python runtime. The npm workspace, the CI job (`test-agent-ts`), the spawner's `anygarden-agent-ts`/`npx` branch and the `invalid_runtime` path were still there, costing maintenance for code nothing could reach.

## Task

- Delete `packages/agent-ts` and its npm, CI and uv workspace wiring.
- Remove the runtime selector across DB, API, lifecycle, the sync frame and the machine spawner, so every agent process is the Python `anygarden-agent`.
- Keep mixed server and daemon versions working, and don't break API clients that still send `runtime`.
- Constraint: the package was never published to npm (`npm view` → 404).

## Action

- Removed `packages/agent-ts/` (30 files). Dropped it from the root `package.json` workspaces and deleted the `test:ts`/`build:ts`/`lint:ts` scripts. Regenerated `package-lock.json`; it only loses entries and adds no new versions or paths. Removed the uv `exclude` in `pyproject.toml` and the `test-agent-ts` job in `.github/workflows/ci.yml`.
- `packages/cluster/anygarden/db/migrations/versions/080_drop_agent_runtime.py`: drops `agents.runtime` and logs a warning with the count of non-`python` rows before dropping. The downgrade re-adds the column with `server_default="python"`. Removed the column from `db/models.py`.
- `api/v1/agents.py`: removed `runtime` from `AgentCreate`, `AgentUpdate` (with `runtime_set`) and `AgentOut`, plus the runtime validator and the update/start checks. `engines/validation.py`: deleted `engine_runtime_error` and `PYTHON_RUNTIME_REQUIRED`. `scheduler/lifecycle.py`: the pre-spawn checks now use only `removed_engine_error`, and the sync frame no longer carries `"runtime"`. `agent_availability.py`: removed `INVALID_RUNTIME` and its message.
- Machine: removed the `runtime` field from `protocol/frames.py`, from `SpawnManifest` and from the daemon's `SpawnManifest(...)` construction. In `spawner.py`, removed the typescript branch and the runtime-based provider/endpoint guard. The Python resolution order (interpreter sibling → PATH → uvx) is unchanged.
- Tests: deleted the runtime-specific tests in `test_agents_api.py`, `test_removed_engines.py`, `test_spawner.py` and `test_local_node.py`. Added `test_create_agent_ignores_legacy_runtime_field`. Moved the migration head pointers to 080 in `test_migrations.py` and `test_federation_trust.py`.
- Docs: `README.md`, `CONTRIBUTING.md` (three packages) and `docs/runbook/retired-engines.md` (removed the runtime-repair instruction).

## Decisions

- Options weighed (see `.tmp/plan-746-remove-agent-ts.md`):
  - A1: keep the column and wire field and only block new `typescript` values. No migration, but a single-valued field and the `runtime_set` repair API would stay forever.
  - A2: rewrite `typescript` rows to `python`. This would auto-start agents that #679 deliberately left blocked. It also buys nothing, since there are no such rows.
  - A3: remove the column, API fields and wire field entirely. **Chosen.**
- The first draft picked A1 to protect `typescript` rows that might exist in other deployments. It switched to A3 after the user confirmed that no agent ever ran on agent-ts. The local live DB also held only `python`/`codex-cli` rows.
- Rejected keeping a daemon-side "refuse non-python frame" guard. Older servers already refuse codex/pi + non-python combinations (#679), so a typescript frame can't realistically arrive. Keeping the field only to guard against it would defeat the removal.
- Compatibility rests on pydantic's default `extra="ignore"` for the frame and API models. If either model switches to `extra="forbid"`, clients or daemons that still send `runtime` will start failing.
- Revisit if the assumption "no non-python rows anywhere" turns out wrong. Migration 080 logs a warning when such rows exist; those agents would then start with `anygarden-agent`.

## Result

- No code path offers a runtime choice any more. The API response no longer contains `runtime`, and requests that include it still succeed.
- Tests: machine 578 passed, agent 667 passed. Cluster full run: 2025 passed and 3 failed. One failure was a missed migration-head pointer in `test_federation_trust.py`, which is now fixed; that file plus `TestAgentCausalLink` then passed (168). The other two were `test_ws_handler` causal-link cases that passed on rerun; they don't touch runtime and are consistent with the known Linux flakes.
- Migration 080 was checked with an upgrade/downgrade/upgrade round trip on a scratch DB, and the warning fires for a seeded `typescript` row. `npm ci` and the frontend `npm run build` succeed.
- Side effect: dropping `runtime` changes the sync-frame hash. An agent whose config is edited before its next spawn may restart one extra time. Live nodes need stop → `anygarden server migrate` → start.
- Pending: PR, and the follow-up package merges (cluster + machine, then agent).
