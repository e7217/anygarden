# feat(rooms): stage agent message + file-chip widths (#329)

- Commit: `24c858e` (24c858e1b391fcf5967c92eeab9f95ad13a81d6d)
- Author: Changyong Um
- Date: 2026-04-30 (Phase 2 of #329)
- PR: pending (issue #329)

## Situation

Phase 1 (#330) staged the right rail across breakpoints, reclaiming horizontal room for the chat canvas below `xl`. That exposed the next mismatch: the agent message branch in `MessageBubble.tsx` used `w-full`, so any markdown, code block, or table inside an agent reply ran edge-to-edge of the chat column. On narrow viewports this matched the same width-asymmetry that drove #322/#324/#326/#328 — fixed-width content forced into a too-narrow rail or column with no gutter.

## Task

- Cap the agent bubble's width with a staged ladder so it still acts as the wider info container but never reaches the column edge.
- Stage the shared-file chip width too, so chips don't push the bubble edge on small screens and can show longer file names on desktop.
- Leave the card variants (handoff / room_query result / room_query forward) untouched — they are intentional full-width cards, not text bubbles.

## Action

- `packages/cluster/frontend/src/components/MessageBubble.tsx:425-431`
  - Changed agent branch from `w-full` to `max-w-full sm:max-w-[90%] md:max-w-[85%] lg:max-w-[80%]`. Non-agent (orphan / guest fallback) keeps the user-side ladder `max-w-[85%] sm:max-w-[75%] md:max-w-[70%]`.
  - Added a comment block referencing #329 Phase 2 explaining the intent (info-container role, why agent is one step wider than user).
- `packages/cluster/frontend/src/components/MessageBubble.tsx:71`
  - Changed shared-file chip `max-w-[240px]` to `max-w-[180px] sm:max-w-[240px] md:max-w-[320px]`. The chip's `truncate` + `title` combo handles the overflow case at every step.
- Card variants (`:266`, `:297`, `:342`) explicitly left at `w-full` — they wrap full-width card components, not bubbles.
- Code-block wrap policy in `index.css` `pre` rules deliberately deferred. The rail-staging + bubble-staging changes alone reclaim enough horizontal room that the existing `overflow-x: auto` is no longer pathological in the ranges this PR targets. If we still see horizontal scrollbars in narrow code blocks after Phase 1+2 ship, that becomes a follow-up rather than blocking this PR.

## Decisions

The plan (`.tmp/plan-329-frontend-responsive-layout.md`) had three options for the agent-bubble width:

- **B2** — apply the user-side ladder verbatim (`max-w-[85%] sm:max-w-[75%] md:max-w-[70%]`). Rejected: agent replies routinely contain tables, multi-line code blocks, and markdown lists that need real horizontal room. Forcing them to 70% on `md` would push that content into avoidable horizontal scrolling.
- **B3 (chosen)** — staged ladder one step wider than the user side: `max-w-full sm:max-w-[90%] md:max-w-[85%] lg:max-w-[80%]`. Agent replies still occupy the visual "info container" role but stop reaching the column edge once the viewport gets large enough to show a gutter.
- **B4** — Tailwind v4 container queries. Rejected for this PR: scope creep. Container queries would let us key off the chat column width rather than the viewport, but the viewport-keyed ladder closes 95% of the gap with one className edit.

The decisive observation was that the `w-full` was load-bearing only for one thing — keeping agent replies visibly wider than user replies. A staged ladder preserves that asymmetry while restoring a gutter, with no behavior change for the card variants which are the legitimately-full-width cases.

Assumptions to revisit if violated:

- Tables and wide code blocks inside agent markdown remain readable at `lg:max-w-[80%]`. If users complain about cropping, the next move is either a wider `lg`/`xl` step or per-element overrides in `MarkdownContent` — not abandoning the ladder.
- The shared-file chip's `truncate` is good enough at 180px on phones. If long file names become unrecognizable at that width, the fix is a tooltip improvement, not a wider chip.

Phase 3 (legacy entry-point cleanup — Search row + 산출물 row in `ChatPage.tsx`) and Phase 4 (breakpoint sweep + 320/480/768/1024/1440 verification) are deliberately split into separate PRs per the plan.

## Result

- 381/381 frontend tests pass; `npm run build` succeeds (existing chunk-size warning unchanged).
- Agent replies now leave a visible gutter on `sm`+ viewports while still acting as the wider bubble.
- Shared-file chips no longer push the bubble edge on phones; can show longer names on desktop.
- No change to handoff/result/forward cards — they remain full-width by design.
- Pending follow-ups: Phase 3 entry-point cleanup, Phase 4 breakpoint sweep + mobile verification, possible code-block wrap policy if narrow-screen scrollbars persist after manual review.
