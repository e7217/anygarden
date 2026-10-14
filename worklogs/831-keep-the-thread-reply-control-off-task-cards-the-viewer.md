# fix(chat): keep the thread reply control off task cards the viewer triggered (#831)

- Commit: `3d55029` (3d55029eefb56da74ca98b56c38b8f367ef949d1)
- Author: Changyong Um
- Date: 2026-10-14T15:48:23+09:00
- PR: #831

## Situation

Thread reply controls in `ChatArea` used `isMine` (i.e., whether the current viewer sent the message) to decide which side of the bubble to render on. However, certain card types — task-assignment, room-query result, and forward cards — always render on the **left** regardless of who sent them. When the viewer triggered one of these cards, `isMine` was `true`, so the reply control took the left side (the empty side of a right-aligned bubble) — which is exactly where these cards sit — and covered the card's title.

## Task

- Fix the side-placement logic for the thread reply control so it follows the bubble's *actual* rendered side, not just `isMine`.
- Ensure plain messages sent by the viewer remain right-aligned while all card types stay left-aligned.
- Add a test to prevent regression.

## Action

- **`packages/cluster/frontend/src/lib/messageLayout.ts`** (new file): Introduced `alignsRight(message, isMine)` — returns `false` for any message that is a task-assignment, room-query result, or forward card (checked via `parseTaskAssignment`, `parseResult`, `parseForward`), and otherwise defers to `isMine`. The JSDoc note explicitly says to keep this in sync with the variant order in `MessageBubble`.
- **`packages/cluster/frontend/src/components/ChatArea.tsx`** (line ~291): Changed the `isMine` prop passed to the thread reply control from `msg.participant_id === myParticipantId` to `alignsRight(msg, msg.participant_id === myParticipantId)`, threading the message through the new helper.
- **`packages/cluster/frontend/src/lib/messageLayout.test.ts`** (new file, 35 lines): Added Vitest tests covering: own plain message → right, other's message → left, task-assignment card triggered by self → left.

## Decisions

- **Separate helper vs. inline ternary**: Extracting `alignsRight` into `messageLayout.ts` keeps `ChatArea` readable and makes the "card types always go left" contract explicit and testable. An inline `msg.metadata?.task_assignment || ...` check directly in the prop would have been harder to maintain as new card types are added.
- **Follow `MessageBubble`'s variant order**: The helper is deliberately coupled to the same card-detection functions used in `MessageBubble` rendering, with a doc comment to keep them in sync. The alternative — re-reading the rendered DOM side — would introduce a React ref / layout-effect dependency with no benefit at this scale.
- **Assumption to watch**: If a new card type is added to `MessageBubble` that also always renders on the left, `alignsRight` must be updated at the same time; the current approach trusts the "keep in sync" note in the JSDoc, not a compile-time link.

## Result

Thread reply controls no longer overlap card titles for task-assignment, room-query result, or forward cards triggered by the viewer. Plain messages sent by the viewer are unaffected. Three Vitest tests cover the corrected behaviour. No pending items.
