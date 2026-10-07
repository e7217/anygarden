# refactor(rooms): remove speaker strategies and the strategy picker (#802)

- Commit: `252d194` (252d194bd6f4fd3e4bd4635c5ea1cd1a8b9ccec4)
- Author: Changyong Um
- Date: 2026-10-07T14:33:08+09:00
- PR: —

## Situation

Rooms carried a speaker strategy (`mentioned_only` / `round_robin` / `orchestrator`, #159). Since #739 made mention-only the product rule, the two non-default strategies ran against it: the server picked a speaker without anyone being mentioned. `orchestrator` also depended on the LLM emitting a `[HANDOFF]` format, which it dropped as conversations grew, so the server kept adding patches (fallback nomination #389, quiet-while-task-open #639). Peer questions (`ask_peer`) and project execution (`delegate_project_task`) already covered the same needs. While planning the removal it turned out that the project operating room's lead workflow (prompt, execution-source session scope, `begin_project_execution` authority, `create_task`) was gated on `speaker_strategy == "orchestrator"`.

## Task

- Remove `round_robin` / `orchestrator`, the strategy picker in the room edit dialog, and the HANDOFF card.
- Drop `rooms.speaker_strategy`, `orchestrator_agent_id`, `next_speaker_participant_id`, `current_speaker_index`.
- Move the operating-lead role off the orchestrator strategy without adding a new user-facing setting (user chose "remove everything").
- Keep message-metadata `next_speaker_participant_id`, which directed delegation (#431 causal link) and interactions still use.

## Action

- Server routing: `expand_room_mentions` and the turn-creation filter apply to every room (`orchestration/rules.py`, `messages/router.py`, `ws/handler.py`). Removed `_apply_orchestrator_handoff`, `_apply_orchestrator_fallback_nominate`, `_compute_round_robin_next` and their dispatcher block. Welcome and `room_settings_changed` drop the strategy fields (`ws/protocol.py`).
- API/model: room payload and PATCH lose the strategy fields (`rooms/router.py`, `api/v1/agents.py`). Migration `091_drop_room_speaker_strategy` drops the columns, and its downgrade restores them with defaults.
- Operating lead: `turns/service.py::_is_operating_lead` stamps `operating_lead: true` on a leased delivery when the agent is the room's representative and the room has an active subroom. The key is reserved in `messages/metadata.py::TURN_PROOF_METADATA_KEYS`, so it cannot be forged. `begin_execution` accepts only `representative_agent_id` (`project_executions/service.py`).
- Removed the orchestrator-only `create_task` MCP tool (`mcp/tools.py`, `mcp/router.py`, Pi self-tool lists) and the orchestrator branch of task sender resolution (`api/v1/tasks.py`).
- Agent: `decide_policy` drops rule 4a and the strategy dispatcher. `client.py` drops strategy caches and `[HANDOFF]` as a task-init prefix. `codex_cli.operating_lead_workflow` and `RoomExecutionAdapter._project_session_scope` now read `metadata.operating_lead`.
- Frontend: RoomEditDialog picker, `HandoffMessageCard`, `lib/handoff.ts`, ChatArea handoff sweep, handoff CSS and the i18n keys are removed. ChatPage shows the mention hint in any room with two or more agents.
- Tests: deleted strategy-only suites (`test_orchestrator_fallback`, `test_handoff_wiring`, `test_create_task_tool`, frontend handoff tests). Kept the roster and settings cache tests as `test_room_welcome_cache.py`. Added tests for the 091 migration, `operating_lead` stamping and reservation, representative-only execution start, `operating_lead_workflow`, the session scope, and the absent picker.

## Decisions

- Options for the operating-lead role:
  - (a) keep a "lead" designation and expand unmentioned messages to the lead
  - (b) remove everything and use the representative
  - (c) remove only `round_robin`

  The user chose (b) so the speaker-strategy concept disappears entirely. QA operating rooms usually hold only the lead, so the #739 one-agent auto-mention keeps "send without mention" working there.
- Signal for the lead on the agent side: a server-derived per-delivery flag (`operating_lead`) rather than a welcome field. The representative is auto-filled with the first agent of every room (`rooms/membership.py`), so "am I the representative" alone would push the operating-room prompt into every room. Only the server can also check that subrooms exist, and per-delivery derivation avoids stale caches.
- `create_task` was removed rather than re-gated on the representative: re-gating would widen task creation to every room's first agent and keep two delegation paths.
- Assumptions: an operating room is a room with active subrooms. If a project needs lead delegation without subrooms, or operating rooms routinely hold several agents (mentions required), revisit.

## Result

- Every room is mention-only. Former `round_robin`/`orchestrator` rooms need mentions, and agent runtimes must be upgraded together with the server, because an older Pi runtime requires `create_task`.
- Test runs:

  | Suite | Result |
  |---|---|
  | cluster pytest | 2710 passed, 2 skipped |
  | agent pytest | 669 passed |
  | frontend build, vitest | 818 passed |
  | Playwright e2e | 43 passed |
- Pending: live verification on a running node, and refreshing the local-only QA-02 document for multi-agent operating rooms.
