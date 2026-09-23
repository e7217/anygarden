# fix(agent): name observed vs required CLI version on UNSUPPORTED_RUNTIME

- Commit: `4ab0835`
- Author: Changyong Um
- Date: 2026-09-23
- PR: — (issue #687, split from #685)

## Situation

After the node's global `pi` was upgraded to 0.87.1, every Pi turn failed in ~230 ms with a bare `UNSUPPORTED_RUNTIME`. The adapter pins `pi` to exactly 0.85.1, but neither the activity log nor the UI said so. The operator, who had just configured a direct endpoint, reasonably blamed the endpoint. The machine kept advertising `pi-cli` as available.

## Task

- Make the failure self-explanatory without widening the receipt contract (`FAILURE_CODES` is a closed set that also crosses the federation boundary).
- Warn before an agent is created on a machine whose CLI version would fail the gate.
- Document the supported versions and pin commands.

## Action

- `runtime/execution/{pi,codex}.py`: explicit `SUPPORTED_VERSIONS`; the version gate records the non-secret `--version` output in `observed_version`; `unsupported_detail()` renders the message through the shared `contracts.unsupported_version_detail()`. The exact-match policy is unchanged (`0.85.10` still fails).
- `integrations/room_execution.py`: on an `UNSUPPORTED_RUNTIME` receipt, raise `EngineError("UNSUPPORTED_RUNTIME: pi-cli 0.87.1 is not supported; this build requires 0.85.1")`. The receipt code stays the same; only the local activity-log text is richer.
- `engines/catalog.py` + `GET /api/v1/agents/engines/{engine}/models`: `supported_versions`, plus a workspace test asserting it equals the adapters' constants (the server doesn't depend on the agent package at runtime).
- Frontend: `lib/engineVersion.ts` (exact comparison after extracting the semver token), plus a warning in the *Create Agent on Machine* dialog.
- `docs/runbook/room-execution-upgrade.md`: a "Supported CLI versions" section.

## Result

- Agent 526, cluster 1741, and vitest 543 tests pass; `npm run build` is clean. The new tests cover the Pi/Codex mismatch messages, a missing version, catalog sync, and the dialog warning.
- Follow-ups: #688 (a managed, pinned Pi install so global upgrades and the *Update engine* `@latest` action can't break agents) and #685 (endpoint setup at creation plus model discovery).
