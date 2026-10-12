# feat(agents): change an agent's engine in place (#769)

- Commit: `4c3732a` (4c3732a6c072b1c6cae3fded164e76292016d5c0)
- Author: Changyong Um
- Date: 2026-10-01T02:46:29+09:00
- PR: #769

## Situation

An agent's engine was fixed at creation. The Overview panel rendered it as plain text and `AgentUpdate` deliberately had no `engine` field, so moving an agent from pi-cli to codex-cli meant creating a new agent — losing its id, room membership, DMs, task ownership and memory links.

## Task

- Let an admin switch pi-cli ↔ codex-cli on an existing agent as an explicit lifecycle event, not a silent PUT.
- Re-validate provider/model for the new engine and reject targets the placed machine cannot run.
- Keep the UI short: no duplicate of the Model connection section.

## Action

- `packages/cluster/anygarden/api/v1/agents.py:241` — `AgentEngineChange` body (`engine`, `provider`, `model`).
- `packages/cluster/anygarden/api/v1/agents.py:828` — `PUT /agents/{id}/engine`: rejects same/removed/unknown engines, Pi without a valid provider and non-Pi with a provider (422); checks the placed machine (or any machine if unplaced) supports the engine and Pi native auth (409); resets `reasoning_effort` and the direct endpoint fields; writes `ActivityLog(agent_engine_changed)`; calls `bump_generation`.
- `packages/cluster/anygarden/api/v1/agents.py:461` — extracted `_require_machine_supports`, now shared with `_validated_create_machine`.
- `frontend/src/components/agent-settings/EngineChangeDialog.tsx` (new) — engine + model, provider only for Pi, one-line reset notice, server error inline.
- `OverviewPanel.tsx` — "Change" button next to the engine, shown only when a handler and another available engine exist.
- `useAgents.ts` `changeAgentEngine`; wired through `AgentSettingsDialog`, `Sidebar`, `AdminMachines`; ko/en strings in `i18n/catalogs/admin.ts`.
- Tests: `tests/test_agents_api.py::TestAgentEngineChange` (11), `EngineChangeDialog.test.tsx` (3), `OverviewPanel.test.tsx` (+2), `Sidebar.test.tsx` mock gains `availableEngines`.

## Decisions

- Options weighed (from `.tmp/plan-769-agent-engine-change.md`):
  - A. Dedicated endpoint + confirm dialog — chosen.
  - B. Add `engine` to `AgentUpdate` — rejected: exactly the silent PUT the existing docstring warned against, and it would interleave provider/model validation with unrelated edits.
  - C. "Duplicate with another engine" — rejected: new id means re-linking rooms, DMs, tasks and mentions; users want the same agent on a different engine.
- Tipping point: model/permission edits already follow "mutate → `bump_generation` → respawn", and `_build_sync_frame` reads `agent.engine` from the DB each time (with #516 `ENGINE_MISMATCH` detection), so engine is just another spawn input once validated.
- Direct endpoint settings are reset rather than carried over because `EndpointConfiguration.validate_engine` accepts different protocols per engine (Codex requires Responses).
- Old engine config files (`.codex/` etc.) and Pi auth secrets are left in place so switching back reuses them.
- Assumptions to revisit: the codex session store (`.anygarden-engine-sessions.json`) is read only by codex; a codex→pi→codex round trip may resume stale threads (falls back to fresh if gone). Engine candidates come from `/agents/engines/available`, not the placed machine — the server remains the authority via 409.

## Result

- Admins can switch an agent's engine from Settings > Overview; id, rooms, history, instructions and memory are kept, and the agent restarts on the new engine.
- Verified in a local dev server: pi→codex succeeded (generation bumped, activity log written); switching back to Pi on a codex-only machine showed "Selected machine does not support this engine" in the dialog.
- Cluster suite: 2675 passed (xdist, excluding `test_e2e_scenario`, which passes alone but was flaky in the full parallel run); frontend: 796 tests passed, `npm run build` ok.
- Pending: live-node check that a real agent answers a turn after switching; `ENGINE_REMOVED_MESSAGE` still tells users to create a new agent even though in-place migration now works.
