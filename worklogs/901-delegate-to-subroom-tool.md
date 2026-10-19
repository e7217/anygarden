# feat(projects): add delegate_to_subroom to hand one task to one subroom in a single call (#901)

- Commit: `d371690` (d371690eeee210c6e86eca03ff27f21a98ba44a4)
- Author: Changyong Um
- Date: 2026-10-19T15:52:25+09:00
- Issue: #901

## Situation

To hand one task to one subroom, an operating lead had to call `begin_project_execution`, `delegate_project_task` and `seal_project_plan` in order and copy each result into the next call: `execution_id`, `root_task_id`, the subroom ID, the assignee's participant ID and the child task ID. This is the most common delegation, but it still took three steps, and a model that got the order or a value wrong was stuck. `begin_project_execution` also listed assignees whose agents were not running, which `delegate_project_task` then refused.

## Task

- Add one convenience tool that begins the execution, delegates one task and seals the plan in a single call. Keep the existing tools and guarantees: the DB records, the lead waking when the result arrives, and the lead reviewing and summarising the result through `complete_project_execution`.
- Make it atomic and idempotent. A refusal at any step must leave nothing behind, and replaying the same turn must not create a second task.
- When the subroom or assignee is ambiguous, refuse and list the candidates so the model can retry. Never pick one silently.
- Do not let it join an execution that already plans several tasks or QA.
- Point the operating-lead prompt at the new tool for single delegations, and keep the Pi allowlist, the `tools/list` test, the docs and the requirement catalog in sync.

## Action

- `packages/cluster/anygarden/mcp/project_tools.py`:
  - Added the `delegate_to_subroom` schema. `spec` is required. `subroom`, `assignee`, `title` and `objective` are optional.
  - `_single_delegation_target` uses `_eligible_rooms`, keeps only assignees whose agent has `desired_state == "running"`, and resolves the subroom by ID, then by exact name. When `subroom` is omitted it uses the only subroom with a running agent. The assignee is resolved by participant ID, agent ID or name, and may be omitted only if exactly one candidate is left. Otherwise it raises `ExecutionConflict` with one of `SUBROOM_CHOICE_REQUIRED`, `SUBROOM_NOT_FOUND`, `ASSIGNEE_CHOICE_REQUIRED` or `ASSIGNEE_NOT_FOUND`, plus `candidates` (`subrooms`, `assignees`).
  - `delegate_to_subroom` calls `service.begin_execution(requires_qa=False)`. It refuses with `SINGLE_DELEGATION_ONLY` when the execution requires QA, is sealed with more or fewer than one required task, or already holds a non-root task under a different key. It then calls `service.delegate_task` with `role="work"`, `delegation_key="single:"+sha256(execution:room:participant)[:32]`, and a title that is either the given one or the first 80 characters of the whitespace-collapsed spec. Finally it calls `service.seal_plan([child])`, and skips the seal on a replay that is already sealed with that child. The result is `execution_payload` plus `task_id`, `task_status`, `subroom`, `assignee` and a `note` telling the model to end the turn and later call `complete_project_execution`.
  - The dispatch snapshots room input files as begin does. The shared tail now sends task events for both the root and the child. The error path returns `"CODE: detail"` and `structuredContent{code, detail, subrooms, assignees}`, following the `resume_project_task` pattern.
- `packages/cluster/anygarden/agent/runtime/execution/pi_self_tools.json`: added the tool to `optional`.
- `packages/cluster/anygarden/agent/integrations/codex_cli.py`: in `_OPERATING_LEAD_WORKFLOW`, one task for one subroom now uses `delegate_to_subroom` once. The step tools are reserved for several tasks, dependencies, QA, approvals or external submission. The "answer small requests yourself" and completion guidance are unchanged, and so is the `/delegate` sentence that #899 owns.
- Tests:
  - `tests/test_project_execution_flow.py`: 8 EXE-23 cases. They cover a single-call success with nothing named (root and child task pushes, worker lease), a same-turn replay, a refusal with candidates followed by a by-name retry, a subroom with only stopped agents, an unknown assignee followed by a by-name retry, a lead that is not the representative (`OPERATING_LEAD_REQUIRED`), refusal on a sealed multi-task plan, and result → wake → `complete_project_execution`. The refusal cases also check that no execution or task is left behind.
  - `tests/test_mcp_server_create_skill.py`: updated the `tools/list` name set.
  - `tests/agent/test_integrations/test_operating_lead.py`: added a prompt substring test.
- `docs/requirements.md`: added EXE-23 (pytest). `packages/cluster/CHANGELOG.md`: added an Unreleased › Added entry.

## Decisions

- **Only running agents count as candidates**: `delegate_task` already refuses non-running assignees. Offering them would only produce a second refusal. When `subroom` is omitted, "exactly one candidate" means one subroom with a running agent. That lets the common one-team setup leave out both arguments.
- **Refuse with candidates instead of defaulting to the subroom's representative or the first match**: a silent wrong assignment is worse than one extra call. The structured candidate list makes the retry cheap.
- **Derive the delegation key from execution, room and participant, not from the spec**: replaying the same call returns the same task. Calling again for the same target with a different spec hits the existing `DELEGATION_KEY_CONFLICT`, which is intended.
- **`SINGLE_DELEGATION_ONLY` for executions with other tasks or QA**: sealing `[child]` would drop earlier required work (`REQUIRED_TASK_DROPPED`) or fail `REQUIRED_QA_MISSING`. A clear code that points to the step tools is easier for the model to act on.
- **No auto-completion**: `finish_execution` needs the lead's turn proof and the lead's own review summary, so completion stays with `complete_project_execution`.
- **Deviation from plan**: `docs/features.md` does not exist in the repo. The feature is recorded in the CHANGELOG and the requirement catalog instead.

## Result

- One `delegate_to_subroom` call creates the child task, seals the plan (execution `waiting_children`) and delivers the assignment to the subroom agent. A replay returns the same task.
- Ambiguous or invalid targets are refused atomically with candidates.
- Targeted tests: 73 passed. Full cluster suite (`pytest -n logical`): 3972 passed, 7 skipped, 2 failed. The 2 failures were in unrelated agent runtime timing tests (`test_room_lifecycle_measured_usage[timeout-pi-cli]`, `test_running_process_stops_and_receipt_remains_after_revoke[lease]`), and both passed on a rerun on their own.
- Not done: live verification with a real engine (`verify-scenarios`), i.e. whether the lead actually chooses the new tool and makes fewer calls.
