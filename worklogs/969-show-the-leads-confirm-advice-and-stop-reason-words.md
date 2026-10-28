# feat(goals): show the lead's confirm advice and say why a goal stopped (#969)

- Author: Claude (assisted, user-directed)
- Date: 2026-10-28
- Issue: #969 (items 3b and the card part of 5)

## Situation

In the reviewed goal run the lead's report said "24 distinct ideas, not 30 — I do not
recommend confirming". That advice lived only in the chat body. The confirm button in
the right rail showed nothing of it, and a person confirmed the goal. Separately, #970
removed the reason code ("user") from stop notices, but the card then said nothing
about who or what stopped the goal.

## Task

Make the lead's advice a value the confirm button can show, and have the stop card
explain the stop in words.

## Action

- New campaign tool `advise_goal_confirmation(advice: confirm|hold, reason)`. Only the
  lead's confirmation report turn may call it (the turn's idempotency key must be that
  goal's current `goal-report:<id>:confirmation:<revision>`). It is stored as a
  `confirm_advice` CampaignEvent with the state revision; a later call replaces it.
  No migration.
- `CampaignOut.confirm_advice` is set only while the goal is `awaiting_confirmation` at
  that revision, so it disappears once a person decides.
- The confirmation report brief asks the lead to call the tool once with a one-sentence
  reason.
- The right-rail goal card shows the advice above the confirm buttons: an icon plus text
  (green check for "confirm", amber warning for "hold") and the lead's reason.
- Stop notices carry `goal_status.stop_reason`. The chat card translates it ("사용자가
  중단했습니다", "방을 보관해 중단됐습니다"); the message body still never shows the code.
- Added CMP-20 and extended CMP-16.

## Result

- pytest: advice recorded and shown, replaced by a later call, cleared on reject; refused
  outside the confirmation report turn; the brief names the tool; the stop notice carries
  the code.
- vitest: advice block for hold, none without advice or outside awaiting_confirmation;
  stop-reason parsing; stop card wording.
- Full cluster suite: 4,582 passed. vitest: 1,192 passed. `npm run build` OK.
- Not done: inspecting the UI in a browser at the four reference widths.
