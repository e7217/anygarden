# feat(goals): post a goal's status change to the room chat (#957)

- Commit: `02107d3` (02107d36d848ca54057fb296095a52d635c23781)
- Author: Changyong Um
- Date: 2026-10-28T12:27:52+09:00
- PR: #957

## Situation

A goal's (campaign's) status changes appeared only in the room's right-hand panel. In the 0.27.0 live report, a goal was blocked after repeated evaluation failures (#956), but nobody reading the chat could tell. The only goal feature that wrote to the room was the retrospective (`campaigns/retrospective.py`).

## Task

- When a goal needs a person or ends, post one system message to the goal's room.
- Avoid noise: no message per cycle, and none for pause/resume/edit.
- The notice must not wake agents and must be broadcast only after the surrounding transaction commits.
- Render it as a compact, localized card in the chat.

## Action

- `packages/cluster/anygarden/campaigns/service.py:225` `apply_transition`: after a successful CAS transition whose new status differs from the old one and is in `NOTICE_STATUSES` (`:276` — blocked, plateau, awaiting_confirmation, achieved, deadline_reached, budget_exhausted, stopped), call `_post_status_notice` (`:294`).
  - The notice has `participant_id=None` and an English one-line `content` with no mention.
  - Its metadata is `{"goal_status": {campaign_id, title, status, previous, detail}}`. `detail` is `status_detail` or the transition reason.
  - Shared-channel rooms (`ChannelError`) are skipped.
- `packages/cluster/anygarden/campaigns/notify.py:52`: `Pending.messages` and `mark(message_id=)`. `publish` broadcasts each message with `message_to_frame` (`:123`), on both the after-commit background publisher and REST `commit_and_publish`.
- Frontend:
  - `src/lib/goalStatus.ts` (parser + vitest)
  - `src/components/GoalStatusCard.tsx`: icon + "Goal" label + title + the existing `campaigns.status.*` label + detail, using status tokens
  - a `MessageBubble.tsx` branch
  - `chat.goalStatus` (en/ko)
- Tests:
  - `tests/test_campaigns_cycles.py`: blocked and budget notices once each; none for pause/resume
  - `tests/test_campaigns_notify.py`: stop broadcasts a `message` frame
  - `e2e/campaigns.spec.ts` `[CMP-16]`: card renders
- `docs/requirements.md`: added CMP-16.

## Decisions

- Options (from `.tmp/plan-957-goal-status-chat-notice.md`):
  - **A. Post from `apply_transition` (chosen)**: the one point every transition (scheduler, evaluation, REST, agent report) passes, and it runs only on real status changes guarded by CAS/`state_revision`, so duplicates are impossible.
  - B. Post every cycle result: rejected as noise (a 21-minute cycle goal would post dozens a day).
  - C. Frontend-only toast from `campaign.updated`: rejected. Nothing persists, so a reload, other users and agents reading room history miss it.
- Deciding point: the feedback was "I didn't know from the chat it was stuck". Both people and agents need a durable record, so a stored server message wins.
- Pause/resume excluded because they are the person's own clicks. `stopped` is included because others in the room benefit.
- Content stays English (agents read it). The card localizes only the status label. `detail` is server English like the panel.
- Assumption: goal rooms are project rooms, not shared channel streams. Revisit if goals run in shared channels.

## Result

- Rooms now show a goal-status card when a goal blocks, plateaus, awaits confirmation or ends. No agent is woken.
- Cluster pytest: 4530 passed, 7 skipped. Vitest: 1158 passed. `[CMP-16]` e2e passes (with `CI=1`; the local run without it reuses a stale server).
- Pending: localized `detail` text. Live verification on a node.
