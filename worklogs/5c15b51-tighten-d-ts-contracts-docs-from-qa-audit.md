# chore(design-sync): tighten .d.ts contracts + docs from QA audit

- Commit: `5c15b51`
- Author: Changyong Um
- Date: 2026-06-22
- PR: #477

## Situation

After the initial `/design-sync` import (commit `4e6ca69`) uploaded the 15 UI primitives to
claude.ai/design, a 54-agent multi-agent QA audit ran over the delivered design system — auditing each
component's `.d.ts` accuracy, `.prompt.md` usefulness, preview coverage, and brand adherence, plus
cross-cutting checks (README/conventions validity, token closure, bundle export integrity, a11y). The
audit confirmed (after adversarial verification) 24 component findings + cross-cutting findings. The
system was functional, but the type contracts the Claude Design agent codes against had real gaps.

## Task

- Fix the high-value, cleanly-fixable contract/doc issues without forking the converter or editing app
  source.
- Re-upload the improved surface to the design project.
- Leave the source-level (app) a11y gaps for a separate app-side fix; record them.

## Action

- `.design-sync/config.json` — added `cfg.dtsPropsFor` for 13 components. The extractor had curated
  inline-typed components (`React.HTMLAttributes`/`ComponentPropsWithoutRef` in the forwardRef generic)
  down to an opaque `{ [key: string]: unknown }`, and dropped HTML handlers from Button. The hand-written
  bodies pin accurate props: Button (`onClick`/`disabled`/`type`/`aria-label` + variant/size/asChild),
  Input/Label/Separator/Tabs/ChatInput (real props), Dialog/Tabs controlled-state props, Avatar/Card/Table
  curated subsets with compound-part JSDoc hints.
- `.design-sync/conventions.md` — added a "Compound parts & key patterns" section (compound exports on
  `window.AnygardenUI`, controlled state for Dialog/Tabs, ChatMessageList bounded-height + auto-scroll +
  `smooth`, ChatBubble `layout="ai"` + variant auto-injection, icon-button `aria-label`); corrected the
  type-utility description ("sets size + weight + line-height + tracking", not "size only").
- `.design-sync/previews/Table.tsx` — added a `TableFooter` totals row and a `data-state="selected"` row
  (brand-tint highlight), surfacing two previously-unshown axes.
- `.design-sync/previews/ChatBubble.tsx` — added an `AiMessage` story (`layout="ai"`, full-width
  borderless).
- `.design-sync/NOTES.md` — recorded `dtsPropsFor` as hand-maintained (overrides extraction; update on API
  change) and the source-level chat a11y gaps (ChatBubbleAvatar hardcoded alt, ChatBubbleAction undocumented,
  MessageLoading no a11y) as app-side follow-ups.
- Rebuilt, re-validated (15/15 render clean, all `.d.ts` parse), re-graded Table + ChatBubble `good`, and
  re-uploaded all `.d.ts`/`.prompt.md` + README + the two previews to the project (component set unchanged,
  no deletes).

## Decisions

- **`dtsPropsFor` over forking the emitter.** The skill forbids forking `emit.mjs`/`dts.mjs` output
  contracts; `dtsPropsFor` is the supported per-component override and gives exact, reviewable contracts.
  Rejected: adding compound parts to `componentSrcMap` to type them per-part — would clutter the DS pane
  with non-standalone cards (CardHeader, TableRow); documented them in the conventions header instead.
- **Conventions header as the vehicle for cross-cutting usage guidance.** It is inlined into the design
  agent's system prompt, so compound-part + controlled-pattern + a11y guidance there reaches the agent for
  every component at once — higher leverage than 15 per-component `.prompt.md` edits.
- **Source-level a11y left unfixed deliberately.** ChatBubbleAvatar's hardcoded `alt`, MessageLoading's
  missing SVG role, etc. are app product code; a design sync should not edit it. Surfaced in NOTES + the
  user report for a separate app-side change.
- Assumption to revisit: `dtsPropsFor` entries are now hand-maintained and OVERRIDE extraction — if a
  component's real props change, a stale entry ships a wrong contract silently. NOTES flags this.

## Result

All 15 components now carry accurate `.d.ts` contracts (Button typechecks `onClick`/`disabled`; Input/
Label/Separator/Tabs/ChatInput expose their real props; Dialog/Tabs expose controlled-state props), the
conventions header documents compound parts + controlled patterns + a11y, and Table/ChatBubble previews
cover more axes. Re-validated clean and re-uploaded to the live project (re-anchored). Pending: the three
source-level chat a11y gaps remain for an app-side fix (recorded in NOTES).
