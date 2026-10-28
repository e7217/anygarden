# fix(goals): run the evaluator in the agent's work dir and count late usage (#956)

- Commit: `0f0d6f3` (0f0d6f34ba87ca8af1462ff811cde2b487c732f0)
- Author: Changyong Um
- Date: 2026-10-28T12:21:41+09:00
- PR: #956

## Situation

In v0.27.0 live, goals managed by a codex-cli PM failed every evaluation cycle. The machine daemon was running the evaluator from the agent's root directory (`<agents_root>/<agent_id>/`), but codex-cli places its workspace at `workspace/` beneath that root, so every pin path and cwd reference resolved against the wrong directory, producing `pin_missing` failures. Separately, engine usage is settled asynchronously after the run terminates; the goal's token sync ran before settlement, leaving `tokens_used` at zero so the budget limiter never fired. Two additional independent bugs: `workspace_action` responses returned as plain dicts but `_brief()` tried to read them via `getattr`, giving null `id`/`status` fields; and the evaluator contract's required `"valid"` boolean was undocumented in the goal brief, tool schema and UI hint, so agents wrote evaluators that omitted it.

## Task

Fix four independent defects so that codex-cli goal evaluation works end-to-end:

1. **Evaluator working directory** — resolve the evaluator's cwd, pins and command under `<agent_root>/workspace/` (read from the manifest), not under the bare agent root.
2. **`valid` documentation** — add the required `"valid": true/false` boolean to the evaluator output contract in all three places where agents and users read it.
3. **Late token accounting** — hook into the usage-settlement event so tokens confirmed after the cycle closes are still counted against `tokens_used`.
4. **`workspace_action` results** — make `_brief()` handle plain `Mapping` responses without falling back to `getattr`.

Scope exclusions: room-chat posting (#957), pre-running the evaluator at goal creation time.

## Action

- **`anygarden/machine/campaign_eval.py`**: Added `work_dir: str = "."` field to `EvalJob` (line 97). Updated `workspace_root()` to accept `work_dir` and validate both `<root>` and `<root>/<work_dir>` as real, non-symlink directories, raising `EvalRequestError("workspace_missing")` for any invalid path. `run_eval()` now passes `job.work_dir` to `workspace_root()` so all path resolution (cwd, pins, command) happens under the agent's actual working directory.

- **`anygarden/machine/daemon.py`**: Extracted `_eval_job()` helper that loads the agent's manifest from `self._manifest_store.load(frame.agent_id)` and passes `work_dir=work_dir_name(manifest.engine)` to `EvalJob`; falls back to `"."` if no manifest exists. `_handle_campaign_eval_request()` now delegates to `_eval_job()` before submitting.

- **`anygarden/campaigns/lifecycle.py`**: Added `on_usage_settled(db, execution_id, *, clock)` (line 464). It looks up the latest `CampaignAttempt` for the execution, then calls the existing `_sync_tokens` → `_add_tokens` pipeline, which is already idempotent (adds only the increase above the recorded amount). Updated `_sync_tokens` docstring to remove the "Known v1 gap" paragraph and describe the three call sites.

- **`anygarden/ws/handler.py`**: After `settle_native_usage` succeeds with `summary_changed=True`, calls `on_usage_settled(db, execution_id)` in the same transaction.

- **`anygarden/workspace/actions.py`**: In `_brief()` (line 53), added a `Mapping` branch between the `BaseModel` branch and the `getattr` fallback so plain dict responses from `campaign_view()` are passed through directly.

- **`anygarden/workspace/drafts.py`**: Updated `_goal_brief()` item 2 to include the example `{"score": 22, "valid": true}` and a note that `valid: false` signals an untrustworthy measurement.

- **`anygarden/mcp/draft_tools.py`**: Updated the `evaluator.command` description in `DRAFT_TOOL_SCHEMAS` to mention the `"valid"` boolean and show the example object.

- **`packages/cluster/tests/`**: Added regression tests in `test_campaign_eval.py` (work_dir resolution: correct path, symlink rejection, missing dir), `test_campaigns_cycles.py` (late token settlement after cycle close, idempotent re-settlement, budget stop on late overage), `test_workspace_tools.py` (`create_goal`/`pause_goal` results carry non-null `id`/`status`), and `test_drafts.py` (goal brief contains `"valid"`).

## Decisions

**Decision A — where to resolve the evaluator's working directory (chose machine-side manifest lookup)**

Three alternatives were weighed:
- **A1 (chosen)**: Daemon reads its own manifest at eval time; no protocol change; the machine already owns layout knowledge via `work_dir_name()` used in `spawner.py` and `workspaces/moves.py`.
- A2: Server embeds `work_dir` in `CampaignEvalRequestFrame` — cleaner trace logs but requires schema migration and leaks layout knowledge to the server; incompatible with already-deployed machines.
- A3: Server stores `evaluator.cwd = "workspace"` at goal creation — minimal code change but pin paths would also need adjusting, and existing goals would not benefit.

The decisive factor: layout is a machine-side concern, the single source of truth is `machine/workspace_import.py:work_dir_name()`, and the manifest is always present for any placed agent at evaluation time. If an agent is not placed the server already holds the request at `agent_not_placed` before dispatching. Assumption: if multi-machine placement without manifests becomes a scenario, revisit A2.

**Decision B — how to surface the `valid` contract (chose documentation-only fix)**

The contract itself (`valid` required, `valid: false` = untrusted measurement) was intentional from #930. The gap was purely in three documentation surfaces. Pre-running the evaluator at `propose_goal` time (option B3) would be the most reliable guard but requires a machine round-trip and draft-flow changes — deferred as a separate task.

**Decision C — when to sync late tokens (chose usage-settlement hook)**

Live timeline showed: run terminal at 02:46:48 → cycle closed at 02:46:49 → usage settled at 02:46:52. Settlement is always last. `_sync_tokens` is idempotent (records the high-water mark), so calling it a third time at settlement is safe. Polling the scheduler (option C3) would introduce latency and rescan closed cycles unnecessarily.

**Decision D — where to fix `workspace_action` dict results (chose `_brief()` helper)**

Changing the eight call sites to return `CampaignOut` models was considered but `campaign_view()` is shared across multiple response paths. Adding a `Mapping` branch to `_brief()` is the narrowest possible change with no call-site impact.

## Result

- Evaluator runs under the agent's actual workspace (`workspace/`) so pin and cwd paths match what the agent sees; `pin_missing` failures on codex-cli goals are eliminated.
- `tokens_used` is updated when engine usage is finally settled, regardless of whether the evaluation cycle already closed; the goal budget limiter fires correctly on the next scheduler tick.
- `workspace_action` goal results (`create`, `pause`, `resume`, `stop`) now carry non-null `id` and `status` fields.
- All three documentation surfaces (goal brief, `propose_goal` tool schema, frontend form hint) now state that the evaluator's last stdout line must be `{"<score_key>": <number>, "valid": true|false}`.
- 12 files changed, 181 insertions, 21 deletions; 4 new test files/extensions covering the fixed paths.
- Pending: live verification with a real codex-cli goal on the integration node (verify-scenarios or manual); release note should flag that existing codex goals with stored pin paths relative to the agent root may need their evaluator re-pinned after this deploy.
