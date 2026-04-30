# fix(rooms): contain right rail viewport overflow at the substrate (#336)

- Commit: `457f091` (457f0914fb77851d1b6c8aa3f45a780d48a9bc90)
- Author: Changyong Um
- Date: 2026-04-30T23:05:45+09:00
- PR: #336 (issue)

## Situation

Right context rail (`RightContextRail`) clipped its rightmost ~15px in
테스트룸4 at 1440px viewport: agent assignee chips on tasks were rendered
as `agent01-code` instead of `agent01-codex`, and the long shared file
name `[LIVE] 전국민 AI 경진대회(인공지능 챔피언 대회) 사업설명회.txt`
escaped the rail's column. #335 had attempted to fix this by adding
`min-w-0` defenses to `TasksSection` rows (closing #334), but the
overflow re-emerged the moment FilesSection carried a long filename —
proving the leak was upstream of any single section.

DevTools measurement at 1440px: viewport `clientWidth=383`,
`scrollWidth=398` (FilesSection trigger). Synthetic long task title
ballooned `scrollWidth` to 676.

## Task

- Identify the actual layout substrate causing `min-w-0` / `truncate`
  to fail in **any** rail section.
- Apply a single fix that handles every current and future trigger
  without per-section row patching.
- Keep #335's section-level defenses as defense-in-depth.
- Avoid introducing visual regressions in the other ScrollArea consumers
  (left Sidebar, ChatArea).

## Action

- `packages/cluster/frontend/src/components/ui/scroll-area.tsx:14` —
  added `[&>div]:!block [&>div]:!min-w-full` to
  `ScrollAreaPrimitive.Viewport`'s `className`. This Tailwind arbitrary
  variant overrides Radix's inline `display: table; min-width: 100%`
  on the viewport's first-child wrapper with `display: block` while
  preserving the `min-width: 100%` invariant. A multi-line comment
  documents the mechanism and links #336.
- `packages/cluster/frontend/src/components/ui/scroll-area.test.tsx`
  (new) — vitest+jsdom regression guard pinning the override classes
  on the rendered viewport. jsdom can't measure layout, so this test
  asserts the className contract rather than computed dimensions; the
  full layout assertion lives in the manual Playwright check below.
- Manual verification (Playwright + DevTools) at 1440px:
  - 테스트룸4: all three viewports (Sidebar 255, ChatArea 800, RightRail
    383) report `clientWidth === scrollWidth`. Inner wrapper computed
    `display: block`, `min-width: 100%`. Long filename now truncates
    cleanly with ellipsis; agent chips render in full.
  - Synthetic long task title injected: `scrollWidth` stays at 383
    (was 676 pre-fix).
  - 테스트룸1 baseline: no visual regression.
  - All 385 frontend unit tests pass; build succeeds.

## Decisions

Considered four approaches (see `.tmp/plan-336-*.md` §3.2 for the full
matrix):

- **A. Substrate fix in `ScrollArea.tsx`** — chosen. One className
  edit covers every consumer.
- **B. Per-row `w-0 min-w-full` patches in each Section** — rejected
  because #334/#335 already proved this strategy reactive: a new
  trigger (long filename) re-broke the rail right after the Tasks
  patch landed. Sidebar's project/room rows carry the same latent
  pattern.
- **C. Replace Radix ScrollArea with native `overflow-y-auto`** —
  rejected. Sidebar and ChatArea would diverge visually from the rail,
  violating DESIGN.md's "single accent, consistent chrome" principle.
- **D. Global CSS rule on `[data-radix-scroll-area-viewport] > div`** —
  rejected. Couples to Radix's internal attribute names and breaks
  silently on a minor version bump; cascade is harder to reason about
  than a colocated component-level utility class.

Decisive evidence for A: the same min-content bug manifested from two
unrelated triggers (filename, task title) within days — a pattern that
keeps re-paying maintenance cost unless fixed at the layout substrate.

Assumptions to revisit if violated:
- Radix `Viewport`'s first child stays a `<div>` (verified for
  `@radix-ui/react-scroll-area@^1.2.0`). On v1.3+ rewriting the
  selector to `[&>*:first-child]` is the next-cheapest move.
- The `!important` arbitrary-variant pattern stays scoped to this
  component (Tailwind makes it local to `Viewport`'s className,
  not a global cascade override).

## Result

- Right rail truncation works in every section regardless of inner
  text length, at all staged widths (288 / 320 / 384).
- Three ScrollArea viewports verified `clientWidth === scrollWidth`
  in 테스트룸4 with the long-filename trigger active; previously the
  rail viewport was off by 15px.
- 385/385 frontend unit tests passing; new substrate guard added at
  `scroll-area.test.tsx`.
- #335's TasksSection defenses retained as defense-in-depth.
- No visual or behavioral regression in Sidebar / ChatArea.
- Closes #336.
