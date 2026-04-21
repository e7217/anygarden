# fix(agent,cluster,frontend): surface participant roster to handoff_to and broadcast room settings (#221)

- Commit: `8c7a022` (8c7a022c30d4712b3b35b26a4b6810d9857ce1b7)
- Author: Changyong Um
- Date: 2026-04-21T21:07:13+09:00
- PR: #221

## Situation

`speaker_strategy="orchestrator"` was wired into the server and the agent SDK (#159 Phase C) but never actually worked end to end. Two independent gaps kept it dead:

- The Claude Code adapter's `handoff_to` MCP tool took `{"participant_id": str}`, but the LLM had no channel to learn valid UUIDs. Welcome, system prompt, and message content all omitted the roster, so the LLM filled the slot with display names like `agent01-codex`. The server's `_apply_orchestrator_handoff` then failed the DB lookup and silently returned `None`, so `next_speaker_participant_id` was never set and every handoff attempt no-oped.
- `PATCH /api/v1/rooms/{id}` persisted `speaker_strategy` / `orchestrator_agent_id` / `context_window_enabled` but never notified connected clients. Agents cached those values only from the welcome frame, so a live admin toggle left already-running agents on the old strategy until they reconnected.

## Task

- Give the orchestrator LLM a stable, UUID-annotated source of room participants so `handoff_to` can be called with values the server will accept.
- Propagate mid-session admin PATCHes to the three cached-at-welcome fields without forcing agents to reconnect.
- Do this without expanding the PR's blast radius: no new runtime dependencies, no forced agent restart, no rewrite of the `[HANDOFF]` server parser.

## Action

Server:

- `packages/cluster/doorae/ws/protocol.py` — added `ParticipantBrief(id, display_name, kind, agent_id)`; extended `WelcomeOut` with `participants: list[ParticipantBrief] = []`; added `RoomSettingsChangedOut(room_id, speaker_strategy?, orchestrator_agent_id?, context_window_enabled?)` and listed it in `OutgoingFrame`.
- `packages/cluster/doorae/ws/handler.py` — introduced `_build_participants_brief(db, room_id)` using `selectinload(Participant.user/.agent)` and ordered by `joined_at, id`; stamp the roster on every `WelcomeOut` during the existing room-lookup session.
- `packages/cluster/doorae/rooms/router.py` — `update_room` now receives `request: Request`; after `db.commit()` it emits `RoomSettingsChangedOut` through `request.app.state.connection_manager.broadcast` when any of `speaker_strategy` / `orchestrator_agent_id` / `context_window_enabled` was touched. Rename-only PATCHes skip the broadcast entirely.

Agent:

- `packages/agent/doorae_agent/client.py` — added `_participants_by_room: dict[str, dict[str, dict]]`; populated from `welcome.participants` in `_process_frame`; added a `room_settings_changed` branch with partial-update semantics (non-None values overwrite cached `_speaker_strategy[room_id]` / `_orchestrator_agent_id[room_id]`).
- `packages/agent/doorae_agent/integrations/claude_code.py` — added `_build_roster_suffix(room_id)` which composes Markdown lines of the form `- <@user:{uuid}> {name} ({kind})`, excluding self. `_build_options` appends that suffix to `system_prompt` only when `_is_orchestrator_of(room_id)` holds, so worker agents and pre-#221 servers see no prompt change.

Frontend:

- `packages/cluster/frontend/src/components/RoomEditDialog.tsx` — added a transient `successFlash` state. On successful PATCH the dialog now shows a short neutral banner ("설정이 저장되었습니다. 접속 중 에이전트에 실시간 전파됩니다.") for 1.4s before auto-closing, replacing the immediate close so the admin sees confirmation that the broadcast happened.

Tests:

- `packages/cluster/tests/test_ws_handler.py::TestWelcomeParticipantsRoster::test_welcome_includes_room_roster` — verifies the user + agent roster entries, display name fallbacks, and the `agent_id` shape.
- `packages/cluster/tests/test_rooms.py::TestRoomSpeakerStrategy` — added `test_patch_broadcasts_room_settings_changed` and `test_patch_rename_only_does_not_broadcast` via a `ConnectionManager.broadcast` spy on `app.state`.
- `packages/agent/tests/test_speaker_strategy_welcome.py` — added `TestParticipantsRosterCache` (welcome → cache) and `TestRoomSettingsChangedFrame` (partial update semantics) using the existing `_process_frame` harness.
- `packages/agent/tests/test_integrations/test_claude_code.py::TestOrchestratorRosterPrompt` — asserts orchestrator-only roster injection, self-exclusion, and that an absent roster keeps `system_prompt` verbatim.

## Decisions

Rationale is preserved in `.tmp/plan-221-orchestrator-handoff-fix.md` §3.2; condensed here.

Handoff UUID delivery — four alternatives weighed:

- **Welcome roster + system_prompt injection (chosen).** Pays one roster lookup per welcome; `_build_options` already rebuilds the system prompt every turn, so the injection hook was natural. Gives the LLM *who is in the room* on top of *what the valid UUID is*.
- **Server-side `[HANDOFF]` parser accepts `@Name` legacy mentions.** Zero client churn, but `Participant.display_name` has no uniqueness constraint (verified by grepping the models — no `UniqueConstraint`), so a collision silently sends the wrong handoff. Deferred to a follow-up as a *safety net* orthogonal to the prompt fix.
- **Welcome roster + server fallback combined.** Right answer long term, but doubles the surface under review. Keep the server parser change out of this PR to keep the reviewer's budget focused on the prompt path.
- **Fail the tool, return the roster in the error result.** First attempt always fails, and the partial `[HANDOFF]` marker leaks into the room before the retry. Rejected on UX grounds.

PATCH propagation — three alternatives:

- **New `RoomSettingsChangedOut` frame (chosen).** Matches the existing `*ChangedOut` naming (`RoomMembershipChangedOut`, `RoomPinOrderChangedOut`) and carries partial-update semantics (`None` = "not touched"). Clients patch cache fields individually.
- **Re-send `WelcomeOut`.** Same wire cost but semantically wrong — welcome also carries `pending_rooms` and identity scaffolding that should not re-fire on a settings change.
- **Auto-restart the agent via `/api/v1/agents/{id}/start`.** Drops in-flight conversations; tempted by zero new code but rejected because the blast radius is disproportionate to what the admin asked for.

UX — the plan originally specified "Toast". The frontend has no toast library and this PR refused to add one (YAGNI). A 1.4s in-dialog flash before auto-close delivers the same "acknowledged" signal with zero dependency growth.

Assumptions that, if later violated, should trigger revisiting:

- Participant join/leave inside a single WS session is rare for rooms using the orchestrator strategy. The roster is only refreshed at welcome time; a mid-session join would leave the orchestrator's prompt stale. Follow-up issue: `participants_changed` frame.
- `display_name` collisions don't matter for orchestrator behavior (the UUID is authoritative). If we enable server-side name fallback, this assumption flips and a uniqueness constraint becomes load-bearing.
- Orchestrator rooms stay small enough that stamping the roster into every turn's system prompt is not a token-budget concern.

## Result

Orchestrator strategy is now functional end to end: the LLM sees UUID-annotated participants, emits valid `handoff_to` calls, and the server-side dispatch path (unchanged) correctly updates `next_speaker_participant_id`. Admin PATCHes propagate to connected agents over WS, so `speaker_strategy` / `orchestrator_agent_id` changes take effect on the next turn without a reconnect. Tests: 683 cluster + 260 agent pass (the one pre-existing agent failure, `test_openai.py`, reproduces on `main` and is unrelated — `OPENAI_API_KEY` missing in the test env). Frontend `npm run build` passes. Deferred to follow-up issues: `participants_changed` runtime sync, server-side `@Name` fallback in `[HANDOFF]`, and `display_name` uniqueness review.
