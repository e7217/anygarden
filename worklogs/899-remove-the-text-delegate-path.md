# refactor(agent): remove the text /delegate path so subroom delegation only uses project executions (#899)

- Commit: `07efbac` (07efbacc572e46965767da4b435d76a51326cb2d)
- Author: Changyong Um
- Date: 2026-10-19T15:52:36+09:00
- PR: #899

## Situation

There were two ways to delegate work to a sub-room, and agents got opposite instructions about them. The machine-generated AGENTS.md (`machine/spawner.py`, `## Delegation`) told agents to use `/delegate <sub-room> <task>`. The operating lead's turn prompt (`_OPERATING_LEAD_WORKFLOW` in `codex_cli.py`) said text `/delegate` is not an execution and told the lead to use the project execution tools instead. `/delegate` was parsed only from incoming messages, so an agent that wrote it in a reply just posted the text and nothing was delegated. The path itself was also fragile. `agent/integrations/delegate.py` kept the pending result in the parent agent's memory, so a restart lost it. After a fixed 300 seconds it posted a timeout and dropped the result, even while the sub-room agent was still working. Project executions already did the same job durably: they are stored in the database, have no fixed time limit, and wake the lead when results arrive.

## Task

- Remove the text `/delegate` path and the `[DELEGATED]` directed-delegation handling on both the agent and the server, so sub-room delegation goes only through project executions.
- Narrow the shared task-init prefix checks to `[ROOM_QUERY]`.
- Remove the `## Delegation` guidance section and the `sub_rooms` manifest field, which only that section used.
- Keep federation `delegation_id`, the sub-room REST endpoints, `cycle_guard`, `room_query`, and the defensive metadata guards for old stored messages.
- Make sure `tests/test_project_execution_flow.py` passes without changes.

## Action

- Agent:
  - Deleted `integrations/delegate.py` and its hook in `codex_cli.register_room_adapter`.
  - Dropped the "text /delegate is not an execution" sentence from the lead prompt, so no agent guidance mentions `/delegate`.
  - In `ChatClient`, removed `find_sub_room`, `_delegate_result_handlers`, `_delegation_cleanups` and the code that intercepted delegation results.
  - `_is_task_init_content` now recognizes only `[ROOM_QUERY]`.
- `integrations/base.py`: removed the directed-delegation rule from `decide_policy` and narrowed the root prefix fast-path to `[ROOM_QUERY]`.
- `runtime/handler_wrapper.py`: removed `delegation_id` from `dispatch`, `_run`, the queue tuple (the `turn_key` index moved from `item[3]` to `item[2]`) and `_reply_metadata`. Also removed the delegated-cancel notice (`_CANCELLED_NOTICE`), the delegation branch of the empty-reply check, and the delegation exclusion from server-side transient retry.
- `ws/handler.py`:
  - Removed `_directed_delegation_target`, `is_delegation_result`, the replay skip and the `directed_target_pid` nomination.
  - The #431 peer-turn fan-out now covers only thread replies that mention agents, which is unchanged behavior: root nomination only ever came from directed delegation. `_make_out` loses its delegation-target exclusion.
  - Removed the imports that became unused (`UUID`, `AGENT_EXECUTION_ROLES`).
- `orchestration/peer_ask.py`: `turn_hop` no longer treats delegation metadata as hop 1. Federation messages have no participant, so they still count as hop 1.
- Machine: removed the `## Delegation` section from `_compose_agents_md`, and removed `sub_rooms` from `SpawnManifest`, `SyncDesiredStateFrame`, the daemon and the lifecycle frame builder.
- Tests:
  - Deleted `test_delegate.py` and the delegation-specific tests in `test_ws_handler.py`, `test_client.py`, `test_should_respond.py`, `test_peer_ask.py` and `test_mcp_ask_peer.py`.
  - Kept the queue identity, stale rejection and leased-reply completion tests, now keyed on `request_id`.
  - Changed the legacy `[DELEGATED]` tests to assert that the prefix is now a plain message.
  - Added a test that a frame with a legacy `sub_rooms` field is still accepted, and an assertion that AGENTS.md has no `/delegate`.
- Docs:
  - `docs/requirements.md` ENG-05: a cancelled turn no longer leaves a notice in the room.
  - `packages/cluster/CHANGELOG.md`: added a breaking-change entry.
  - Updated the agent `architecture.md` and `README.md`.

## Decisions

- **Remove the `sub_rooms` field instead of keeping it unused**: `SyncDesiredStateFrame` has no `extra="forbid"`, so pydantic ignores unknown fields. An older server that still sends `sub_rooms` keeps working, and an older machine falls back to its default empty list. A test covers the first case.
- **Keep the REST `delegation_target_participant_id` rejection and the `delegation_outcome` stripping**: these protect old stored messages and stop forged control metadata. The plan kept them on purpose. The REST error text still says "Directed delegations require the agent WebSocket".
- **Simplify the nomination code instead of leaving dead branches**: `nominated_pid` could only ever be the directed-delegation target. With that gone, the root-message branch always produced no peer turn, so `next_agent_parts` reduces to the thread-reply case without changing behavior.
- **Assumption**: nobody types `/delegate` in chat in real use. There is no frontend entry point for it. The CHANGELOG tells users to ask the operating room's lead instead.

## Result

- No agent guidance mentions `/delegate`, and sub-room delegation runs only through project executions.
- 997 lines removed and 123 added across 25 files.
- Full cluster suite (`uv run pytest -n logical`): 3972 passed, 7 skipped, 2 failed. The 2 failures were load-sensitive timing tests (`test_room_lifecycle_measured_usage[timeout-pi-cli]` and `test_running_process_stops_and_receipt_remains_after_revoke[lease]`), and both passed 3 times out of 3 when run alone. `tests/test_project_execution_flow.py` passes without changes.
- Ruff reports no new findings in the changed files compared with `origin/main`.
