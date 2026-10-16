# feat(ui): let agent replies fill the chat column and widen it when side panels are collapsed (#856)

- Commit: `2c66534` (2c66534201e2310a207db1591636727388f8ce20)
- Author: Changyong Um
- Date: 2026-10-16T14:56:54+09:00
- PR: #856

## Situation

The chat conversation column was using a hardcoded `max-w-3xl` (768px) at each of the three independent sites — the message list, the empty state, and the composer — so they were not guaranteed to align. Agent replies were displayed as bubbles with a `max-w-[80%–90%]` cap (introduced in #329 to prevent code blocks running edge-to-edge on narrow viewports), which left a visible empty strip beside long replies and created inconsistent column widths across panel states. There was no shared rule that responded to actual available space when a side panel was collapsed or a thread was opened.

## Task

- Introduce a single `chat-column` CSS utility that all three sites share.
- Make the column width respond to the actual width of `<main>` via a container query, stepping 768 px → 832 px → 896 px at 1040 px and 1200 px container widths.
- Drop the bubble box from agent replies so they fill the column as prose; keep bubbles for human messages, orphaned messages, and deleted messages.
- Keep the typing indicator visually consistent with unboxed agent replies.
- Add an E2E spec (`chat-width.spec.ts`) covering the column behaviour.

## Action

- **`packages/cluster/frontend/src/index.css`** — Added `@utility chat-column` with `max-width: 48rem`, stepping to `52rem` at `@container chat (min-width: 65rem)` and `56rem` at `75rem`.
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`** (`<main>`) — Added `@container/chat` so container queries resolve against the actual `<main>` width.
- **`packages/cluster/frontend/src/components/ChatArea.tsx`** — Replaced `mx-auto w-full max-w-3xl` with `chat-column` on both the empty-state div and the message-list div; added `data-testid="chat-column"`. Simplified the typing-indicator box to `px-1 py-1` (unboxed, matching prose agent layout).
- **`packages/cluster/frontend/src/components/MessageInput.tsx`** — Replaced `mx-auto w-full max-w-3xl` with `chat-column` on the composer inner div; added `data-testid="composer-column"`.
- **`packages/cluster/frontend/src/components/MessageBubble.tsx`** — Introduced `isAgentProse = isAgent && !isOrphan && !isDeleted`; agent-prose messages render with `w-full min-w-0 px-1` instead of a bubble. Human/own messages keep their existing bubble. The `items-stretch` flex layout ensures the prose div expands to column width. Added `data-message-body` attribute (`prose` | `bubble`) for test targeting.
- **`DESIGN.md`** — Documented the `chat-column` utility, its three breakpoints, the container-query approach, and the 896 px cap rationale (Korean prose near 72 characters).
- **`packages/cluster/frontend/e2e/chat-width.spec.ts`** — New spec (239 lines) exercising column width at each breakpoint, panel-collapse behaviour, and prose vs. bubble rendering.

## Decisions

- **Container query on `<main>` vs. viewport media query**: A viewport media query would misfire whenever a side panel is open — the same `1200px` viewport is `1200px` whether the right rail is showing or hidden. A container query on `<main>` responds to the space actually available to the conversation, so collapsing a panel, resizing the right slot, opening a thread, and resizing the window all follow one rule without per-panel breakpoint overrides.
- **`@utility chat-column` vs. an inline Tailwind class**: Three independent sites needed the same value; a utility keeps them in sync and makes the three-step progression legible in one place. Replicating an `@container` responsive chain inline would require JIT arbitrary-value syntax at every call site.
- **Dropping the agent bubble vs. keeping it**: #329 introduced the `max-w-[80–90%]` cap to prevent code blocks running edge-to-edge on phones. The `chat-column` utility's `px-6` gutter (from ChatArea) now covers that concern. A box sized to its content left short and long replies at different widths and created a visible gap. Filling the column makes tables, code blocks, and artifact cards all align to the same right edge as the composer.
- **896 px cap**: Keeps a line of Korean prose near 72 characters at typical browser font sizes. Going wider risks readability; the `@container` step at `75rem` (1200 px) provides the extra room only when the layout genuinely has it.
- **People/orphan/deleted messages keep their bubble**: Human conversation still reads as chat with spatial direction cues. Changing both sides would require a full chat-layout redesign out of scope for this issue.

## Result

- Agent replies now fill the full conversation column width as unboxed prose, aligning with the composer and message list edges.
- The column steps 768 px → 832 px → 896 px as `<main>` grows, driven by a single container query rather than per-component media queries.
- Human messages retain their bubble layout.
- E2E spec added to catch column-width regressions at each breakpoint and panel state.
- DESIGN.md updated with the `chat-column` spec and rationale.
