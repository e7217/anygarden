# fix(agent/openhands): capture user-facing reply from FinishAction tool path

- Commit: `1237d97` (1237d97ea642434a02ee524b799f03ad313d2e9f)
- Author: Changyong Um
- Date: 2026-05-10T11:13:32+09:00
- PR: —

## Situation

After #372 fixed the `MessageEvent` capture schema, `oh-agent04`
(running `openai/qwen3.6:27b`) still produced no user-visible
reply. ActivityLog and gateway logs both showed the LLM call
succeeding (`engine_call_finished outcome=ok duration_ms≈8500`)
and the handler finishing cleanly, yet no `{"type":"message",…}`
frame ever reached the room. Two prior follow-ups (#372 fix,
#369/#371 token cache fixes) closed adjacent failure modes
without changing the symptom.

## Task

- Determine why `_capture_assistant` produced an empty buffer
  despite a successful LLM round-trip.
- Cover the missing event shape without breaking the existing
  `MessageEvent` capture path or any other adapter behaviour.
- Add regression tests so the path is locked.

## Action

- Reproduced the bug via direct WebSocket probe against the
  cluster's `/ws/rooms/{room_id}` endpoint, confirming
  `[message]` frame absence with lifecycle `ok` outcome.
- Added a temporary file-based dump inside `_capture_assistant`
  to record every event the SDK delivered. Trace showed:
  `SystemPromptEvent` → `MessageEvent(source='user')` →
  `ActionEvent(source='agent', action=FinishAction(message='…'))`
  → `ObservationEvent(FinishObservation)`. No agent-source
  `MessageEvent` was ever emitted.
- Cross-checked SDK at
  `openhands/sdk/tool/builtins/finish.py:21-31` — `FinishAction`
  carries the canonical `message: str` field documented as
  "Final message to send to the user."
- Extended `_capture_assistant` in
  `packages/agent/doorae_agent/integrations/openhands_engine.py:449-477`
  with an `ActionEvent` branch gated on `source='agent'` and
  `type(action).__name__ == 'FinishAction'`, pushing
  `action.message` (when non-empty after `strip()`) into the
  captured buffer. Same name-based dispatch as the existing
  `MessageEvent` path — no hard SDK import added.
- Added `TestCaptureFromFinishAction` (4 cases) in
  `packages/agent/tests/test_integrations/test_openhands_engine.py:495-654`:
  happy path, non-Finish action ignored even when it carries a
  `message` attribute, user-source ActionEvent ignored, empty /
  whitespace-only message rejected.
- Removed the temporary dump and `/tmp/oh_capture.log` artifact;
  ran `uvx ruff check` clean and the package's pytest (52
  passed, was 48 pre-fix).

## Decisions

- **Branch on `ActionEvent` vs. listening on `ObservationEvent`
  (`FinishObservation`)**: both carry the text — `FinishObservation`
  in its `content[*].text`, `FinishAction` in `message`. Picked
  the action because the SDK explicitly documents it as the user
  reply ("Final message to send to the user."), the action is
  emitted before tool execution so the buffer is ready when
  `conversation.run` returns, and `FinishObservation.from_text`
  is just a relay over the same string. Fewer indirections, same
  data.
- **Name-based dispatch (`type(...).__name__`) vs. `isinstance`
  against an SDK import**: kept name-based, matching the existing
  `MessageEvent` and Claude-Code adapter conventions. Adding a
  hard import would couple the adapter to a private path
  (`openhands.sdk.tool.builtins.finish`) and break the
  "no SDK installed → adapter degrades to no-op" contract that
  `start()` relies on.
- **Capture vs. patch the `_handle_no_content_response` /
  finish-tool execution path inside the SDK**: rejected.
  Doorae owns the adapter, not the SDK; the finish path is the
  intended termination flow per the SDK's own builtin tool
  description. Trying to suppress it would fight the framework.
- **Trigger to revisit**: if a future SDK release ships a
  different terminator (e.g., `CompleteAction`, multi-message
  finish) or moves `FinishAction` out of the agent-emitted
  ActionEvent path, the gate at line 451 will silently drop
  again. Add another test like
  `test_finish_action_message_captured` against the new shape.

## Result

- `oh-agent04` responds end-to-end on the next user message
  ("저는 OpenHands agent입니다. 컴퓨터 명령어 실행, 코드 수정,
  …") with an actual `{"type":"message",…}` frame and lifecycle
  `outcome=ok`. Verified via WebSocket probe round-trip after
  agent restart.
- 52/52 OpenHands integration tests pass; ruff clean.
- Closes the post-#372 residual symptom for finish-tool-using
  models. MessageEvent-emitting models (Claude / GPT plain-text
  replies) remain unaffected.
