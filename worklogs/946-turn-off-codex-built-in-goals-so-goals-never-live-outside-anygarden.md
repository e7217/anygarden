# fix(codex): turn off Codex built-in goals so goals never live outside anygarden (#946)

- Commit: `ee4acb8` (ee4acb8ec821696dd756b51b06ab7ae08f6a26d7)
- Author: Changyong Um
- Date: 2026-10-27T16:01:32+09:00
- PR: #946

## Situation

Codex CLI 0.164 introduced a stable `goals` feature exposing `create_goal`, `get_goal`, and `update_goal` tools to agents. When an agent was instructed to "use the goal feature," it created a Codex-native goal stored in `CODEX_HOME` — a location anygarden cannot read. This goal was invisible to anygarden's room panels, approvals, budgets, and audit trail, causing agents to mislead users by reporting a "created" goal that anygarden had no record of.

## Task

- Disable the Codex `goals` built-in on every Codex launch path so that goals can only exist within anygarden's campaign system.
- Cover both exec paths: room turns (`CodexCliAdapter`) and the execution runtime (`CodexRuntime`, including fresh and resumed sessions).
- Use a flag syntax compatible with Codex versions that predate the `goals` feature.
- Record the policy in ADR-008 and formalize it as requirement ENG-15.

## Action

- **`packages/cluster/anygarden/agent/runtime/execution/codex.py`** (new code at ~line 36): Added `DISABLED_CODEX_FEATURES: tuple[str, ...] = ("goals",)` and a helper `disabled_feature_args()` that expands each entry into `["-c", "features.<name>=false"]`. The helper is appended to the Codex command in `CodexRuntime.command()` (line ~207).
- **`packages/cluster/anygarden/agent/integrations/codex_cli.py`** (line ~425): Imported `disabled_feature_args` and appended its output to `cmd` inside `CodexCliAdapter._exec_once()`, ensuring room-turn launches are also covered.
- **`packages/cluster/tests/agent/test_integrations/test_codex_cli.py`** (new test ~line 349): `test_exec_disables_codex_goals_feature` — parametrized over `thread_id=None` and `thread_id="existing-tid"` to verify both fresh and resumed room turns include `-c features.goals=false` and never use `--disable`. Tagged `@pytest.mark.req("ENG-15")`.
- **`packages/cluster/tests/agent/test_room_execution.py`** (new test ~line 697): `test_codex_runtime_disables_goals_feature` — checks both `RoomCodexRuntime` and `CodexRuntime` command arrays. Tagged `@pytest.mark.req("ENG-15")`.
- **`docs/decisions/008-two-engine-runtime-contract.md`**: Added "Engine built-in features" section documenting the policy: disable any engine built-in that overlaps an anygarden-managed concept.
- **`docs/requirements.md`**: Added ENG-15 requirement row.

## Decisions

- **`-c features.<name>=false` vs `--disable <name>`**: `--disable <name>` raises `Unknown feature flag` on Codex versions that predate the feature, which would break every agent launch on older deployments. An unrecognized `features.*` key via `-c` is silently ignored (verified on 0.164.0), making `-c` the safer cross-version form.
- **Shared `DISABLED_CODEX_FEATURES` list vs per-call-site flags**: Centralizing the list in `codex.py` ensures both exec paths stay in sync without duplicating the feature names. Adding a future disabled feature requires one edit in one place.
- **Disable rather than intercept**: An alternative would have been to intercept `create_goal` tool calls at the MCP proxy layer and redirect them to anygarden campaigns. That approach was implicitly rejected (per the commit body referencing ADR-008) because it is fragile — the proxy would need to understand Codex-internal semantics — whereas disabling at launch is simple and absolute.
- **Assumption**: If Codex introduces a future `goals` removal or renames the flag, `DISABLED_CODEX_FEATURES` would need updating, but silent ignoring of unknown keys means it would not break.

## Result

Both Codex launch paths now pass `-c features.goals=false` at startup, so the `create_goal`/`get_goal`/`update_goal` tools are never exposed to agents. Two new `@pytest.mark.req("ENG-15")` tests (parametrized across fresh/resumed sessions and both runtime classes) verify the flag is present and `--disable` is absent. ADR-008 documents the general policy for future engine built-ins, and ENG-15 formalizes the requirement. No behavioral changes to any other Codex functionality.
