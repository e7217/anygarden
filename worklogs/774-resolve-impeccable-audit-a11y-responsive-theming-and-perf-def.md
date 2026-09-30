# fix(ui): resolve impeccable audit a11y, responsive, theming and perf defects (#774)

- Commit: `9edd02e` (9edd02eabeb6a97f51fa66ece46318bb48ee9556)
- Author: Changyong Um
- Date: 2026-10-01T03:26:12+09:00
- PR: #774

## Situation

The frontend was audited with the impeccable skill (deterministic detector plus code review across accessibility, performance, responsive, theming and implementation integrity) and scored 14/20. The shared `components/ui` primitives already followed DESIGN.md, but individual components bypassed them: a keyboard-inaccessible file tree, a hand-built search modal, placeholder-only labels, 32px composer buttons on phones, hard-coded amber/black colours, eager-loaded chat pages in the login bundle and 96 arbitrary font sizes.

## Task

- Fix all P1/P2 findings from the audit (issue #774) and the cheap P3 ones, without adding npm dependencies.
- Keep the incumbent design system (DESIGN.md teal tokens, control-height tokens, named type scale) as the authority; refinement, not redesign.
- Keep vitest, Playwright e2e and `npm run build` green.

## Action

- Accessibility
  - `agent-settings/ManifestPanel.tsx`: file/folder rows are real `<button>`s with `aria-current`/`aria-expanded`; hover-only actions also reveal on `group-focus-within`; inputs get `aria-label`s.
  - `SearchDialog.tsx`: rebuilt on the Radix dialog (focus trap, Esc, sr-only title). The focus indicator is the row's bottom border (`has-[input:focus-visible]`), and `outline-none!` is used because the unlayered global `:focus-visible` rule beats layered utilities. New `SearchDialog.test.tsx`.
  - Labels and focus rings in `TaskPanel.tsx`, `Sidebar.tsx`, `AdminMCPTemplates.tsx`, `AdminSkills.tsx` and `RoomInviteDialog.tsx`.
  - `<main>`/`<h1>` in `ChatPage`, `TopologyPage`, `LoginPage` and `GuestInvitePage`; `RoomHeader` room name is now `<h1>`, and `e2e/threads.spec.ts` was updated to match.
  - `ui/card.tsx` `CardTitle` renders `h3` with an `as` prop.
  - Status is shown as text as well as colour in `GoalsPanel` and `GoalsSection`.
- Responsive
  - `MessageInput.tsx` controls use `--control-icon-size`: 44px on touch, 32px on desktop.
  - The composer textarea is `text-base md:text-sm` (no iOS zoom) and has an `aria-label`.
  - `CreateRoomDialog` uses `ui/select`.
- Theming
  - `WorkspaceAttachmentBanner` uses the warning tokens.
  - Backdrops use `--color-overlay`.
  - Fixed rgba shadows became `shadow-whisper`/`shadow-deep`; `text-white` became `--color-on-brand`.
- Performance
  - `App.tsx` lazy-loads ChatPage, the guest pages and FederationPreviewPage, and uses `h-dvh`.
  - `RightContextRail` transitions only transform and box-shadow.
  - Explicit transition property lists replace `transition-all`.
  - AgentNode keeps a constant border width and draws the thicker ring with an outline.
- Motion (`index.css`)
  - Dialog enter animation uses opacity and translateY only.
  - The dead `animate-in`/`zoom` classes were removed from `ui/dialog.tsx`.
  - Global reduced-motion fallback: short transitions, slowed spinners, stopped decorative loops.
- Typography
  - `text-body` defined and registered in `lib/utils.ts`.
  - Arbitrary `text-[Npx]` sizes mapped to the named scale (96 → 2).
  - Form labels and descriptions are at least 14px.

## Decisions

- **Worktree off `main` HEAD instead of editing the main checkout.** The main checkout had unrelated uncommitted work touching the same files (TaskPanel, RightContextRail, ChatPage, ThreadPanel). Isolation keeps this PR reviewable, at the cost of possible merge conflicts with that WIP.
- **Dead dialog animation classes.** Options were: install `tailwindcss-animate`, delete the classes, or add small keyframes in `index.css`. Keyframes won, because they need no dependency and give a working open animation.
  - The first version used `scale(0.97)`. It made e2e size assertions (`agent-setup.spec.ts`) flaky, since the bounding box was measured mid-animation.
  - It was switched to `translateY(4px)`, which keeps element geometry exact. Revisit if tests start asserting position during dialog open.
- **Right rail.** Animating `width` was rejected (it re-lays out the chat column every frame). The width now snaps and only transform animates, so collapse on desktop looks instant. That was accepted as the cost of not thrashing layout.
- **Reduced motion.** A blanket 0.01ms kill was rejected. State changes keep a 100ms transition and spinners slow down rather than stop, per impeccable's guidance that reduced motion must preserve state feedback.
- **Typography mapping.** Named utilities also set a font weight, so `font-normal` was added where the old raw size had no weight. Sizes below 12px were bumped to `text-badge`, except avatar initials in fixed 20/24px circles (`EntityAvatar.tsx`).
- **Detector findings left as-is.** The `side-tab` hits (blockquote in DelegateMessageDialog, thread spine in ThreadInline) and `overused-font` Inter are intentional and sanctioned by DESIGN.md.

## Result

- `npm run build` passes. The login entry chunk dropped from 351.6 kB to 161.9 kB gzip.
- vitest: 100 files, 794 tests pass. Playwright e2e: 41 pass. `agent-setup` was repeated 16× per viewport after the animation fix.
- The impeccable detector went from 4 findings to 3, and the remaining 3 are intentional.
- Manually verified at 1440px light and 375px dark: composer buttons measure 32px and 44px, the mobile input is 16px, and each chat page has one `<h1>` and one `<main>`.
- Pending, out of scope:
  - The unlayered global `:focus-visible` outline overrides `outline-none` utilities app-wide.
  - Search snippets show raw `<@user:uuid>` tokens, and `<mark>` is bright yellow in dark theme.
  - CLAUDE.md still summarises the old Notion palette.
