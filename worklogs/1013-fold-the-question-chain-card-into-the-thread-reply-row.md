# fix(chat): fold the question chain card into the thread reply row (#1013)

- Commit: `bf01162` (bf01162df18c6ee4bf016df2103c104c55d27fec)
- Author: Changyong Um
- Date: 2026-11-10T16:03:16+09:00
- PR: #1013

## Situation

When a question chain was running, the user saw two separate UI elements under the originating message: (1) the thread reply row showing "Reviewer working" with a "Stop" button for the thread's turns, and (2) a `PeerChainProgressCard` showing "Developer is gathering answers · now: Reviewer answering" with a "Stop all" button for the whole chain. This duplication created two parallel progress indicators, two stop controls with different scope, and two entry points into the same thread — a confusing and cluttered UX.

Additionally, the thread panel could open half blank for long reply threads. When following the newest reply via `scrollIntoView()`, the browser's default `block: "start"` alignment would scroll the `overflow-hidden` ScrollArea root alongside the viewport, sliding content under the sticky header.

## Task

- Merge the peer chain progress display into the existing thread reply affordance row for messages that can host a thread
- Show chain activity (including agents answering outside the current room at depth 2+) in the reply row's working text
- Provide a single unified [Stop] that ends both the chain's turns and the thread's other open turns
- Add a hover/focus tooltip (`PeerChainHoverSummary`) that shows the "who asked whom" call tree for pointer-fine devices
- Remove the "depth" indicator from the progress card (COL-17 cleanup)
- Keep the standalone `PeerChainProgressCard` only for messages without a thread reply row (guest view, root messages that cannot host threads)
- Fix the `scrollIntoView()` ancestor-scroll bug in `ScrollArea` by introducing `scrollToEnd()` and using it in both `ChatArea` and `ThreadPanel`

## Action

- **`ThreadReplyAffordance.tsx`**: Added `chains` and `onCancelChain` props. When a chain is running, `workingText` comes from `activityText(t, currentActivity(chain))` (same logic as the card). `since` falls back to the chain's `started_at` timestamp when no thread typing timer exists. `stopChains` fans out `onCancelChain` for every running chain then also calls `onStop` for thread turn IDs. On the row, wrapped the button in a `group/chain` div; appended `<PeerChainHoverSummary>` there. The stop control switches to `<PeerChainStopButton>` (with unified text/description keys) when a chain is present, otherwise falls through to the existing `<TurnCancelControl>`.
- **`PeerChainHoverSummary.tsx`** (new file): A `role="tooltip"` div, hidden by default, revealed on `md:pointer-fine:group-hover/chain:flex` and `group-focus-within/chain:flex`. Builds the call tree via `buildChainTree()`, walks it in a stack-based DFS, renders each node with `caller_name → target_name` and a `<PeerStateBadge>`. Aligns left or right to match the reply row.
- **`PeerChainStopButton.tsx`**: Added optional `text`, `label`, `description` props so the thread reply row can pass its own copy without forking the component.
- **`ChatArea.tsx`**: Computed `chains` and `hostsThread` before the render loop; passes `chains` and `onCancelChain` to `ThreadReplyAffordance`; the standalone `PeerChainProgressCard` renders only when `!hostsThread`. Switched to `scrollToEnd(bottomRef.current)`.
- **`ThreadPanel.tsx`**: Switched to `scrollToEnd(bottomRef.current)`.
- **`scroll-area.tsx`**: Added `scrollToEnd()` — locates the Radix `[data-radix-scroll-area-viewport]` ancestor and calls `viewport.scrollTo({ top: viewport.scrollHeight, behavior })` directly, bypassing ancestor scroll. Falls back to `scrollIntoView({ block: "end" })` if no viewport is found.
- **`peerChains.ts` (i18n)**: Added `peerChains.stop`, `peerChains.stopLabel`, `peerChains.stopThreadDescription`, `peerChains.hover.label` in both `en` and `ko`. Removed `peerChains.progress.depth` (COL-17).
- **`ChatArea.peerChains.test.tsx`**, **`agent-links.spec.ts`**, **`scroll-area.test.tsx`**, **`PeerChainProgressCard.test.tsx`**: Updated tests to cover new props, `hostsThread` branching, hover summary visibility, and `scrollToEnd` behavior.
- **`docs/requirements.md`**: Updated COL-17 requirement entry.

## Decisions

- **Fold into the row vs. a second row**: The card occupied the same visual slot as the reply row and carried the same open-thread interaction. Adding a second row would make the message footer taller for every chain. Folding into the existing row reuses a known, trusted UI pattern.
- **Single [Stop] for both chain and thread turns vs. separate controls**: Two controls with different scope (thread vs. chain) were the core source of confusion. A single confirm-then-stop that fans out to both chains and thread turns keeps the mental model simple: "one stop ends everything under this message."
- **Hover-only summary on pointer-fine devices**: Touch users can tap the row to open the thread and see the full call tree there. A hover summary on mobile is unreachable, and an always-visible list would bloat every active thread row. `md:pointer-fine` gating keeps mobile clean and desktop informative.
- **`scrollToEnd()` at the viewport level vs. `block: "end"` on `scrollIntoView()`**: `scrollIntoView({ block: "end" })` still climbs ancestors and can scroll `overflow-hidden` containers. Scrolling the Radix viewport element directly is explicit and contained. The fallback path keeps existing behavior for non-ScrollArea anchors.
- **Keep `PeerChainProgressCard` for no-thread-row messages**: Guest view and reply roots that cannot host a new thread have no affordance row. Removing the card entirely would silence the chain for those viewers.

## Result

- Running question chains no longer produce duplicate progress UI; the thread reply row is the single source of truth for chain activity under a message.
- Agents answering at depth 2+ (outside the current room) surface in the reply row's working text in real time.
- One [Stop] with a confirmation dialog ends every running chain of the thread plus all open thread turns.
- On pointer-fine devices (desktop), hovering or keyboard-focusing the reply row reveals a tooltip listing who asked whom with per-node state badges.
- The "depth" counter is removed from the main chat (COL-17 requirement addressed).
- The thread panel no longer scrolls half blank when opening to a long thread; `scrollToEnd()` scrolls the ScrollArea viewport itself rather than triggering ancestor scroll.
