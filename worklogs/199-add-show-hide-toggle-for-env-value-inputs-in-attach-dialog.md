# fix(mcp-templates): add show/hide toggle for env value inputs in attach dialog (#199)

- Commit: `d5e07de` (d5e07deb4968b32ef449b2c9191c27fb1143d409)
- Author: Changyong Um
- Date: 2026-04-20T20:45:02+09:00
- PR: #199

## Situation

Admin MCP Servers → Attach dialog renders one password-masked input per `required_env_vars` so that per-agent credentials (GitHub PAT, Slack token, etc.) can be Fernet-encrypted at rest. The inputs were hardcoded to `type="password"` with no way to reveal what was just pasted, making it easy to paste the wrong secret and only notice after a save. The point was raised during a separate discussion about the Template editor's "Secret" checkbox UX — once it was clear that the real value-entry surface is Attach (not the template editor), adding a reveal affordance here became the obvious fix.

## Task

- Add a per-key show/hide toggle to each env input in `AttachDialog`.
- Keep masking by default and keep `autoComplete="off"` so browsers don't offer to remember credentials.
- Reset visibility when the dialog is closed and reopened.
- Leave the Template editor's Secret checkbox semantics untouched — that's a different question for a different change.
- Don't touch the shared shadcn `Input` component; compose the toggle in the caller so other inputs aren't affected.

## Action

- `packages/cluster/frontend/src/components/AdminMCPTemplates.tsx:9` — added `Eye` and `EyeOff` to the existing `lucide-react` import (both icons were already used elsewhere in the frontend, so no new dependency surface).
- `AdminMCPTemplates.tsx:336` — added `showValues: Record<string, boolean>` state next to the existing `envValues` state in `AttachDialog`.
- `AdminMCPTemplates.tsx:429-458` — replaced the flat `<Input type="password" />` render with:
  - `relative` wrapper,
  - `Input` with dynamic `type` (`text` when `showValues[varName]`, else `password`), `pr-9 font-mono` className to leave room for the icon,
  - an `absolute right-0 top-0 h-full w-9` `<button type="button">` containing `Eye`/`EyeOff`,
  - `aria-label` flipping between `Show <VAR>` / `Hide <VAR>` and `aria-pressed` reflecting the current state,
  - ghost styling via `text-foreground-subtle` with a `hover`/`focus-visible` transition to `text-foreground` — no new colors, borders, or shadows.

No tests added (see Decisions).

## Decisions

Plan file: `.tmp/plan-199-mcp-attach-env-visibility-toggle.md`. Three approaches were weighed:

- **Input-internal overlay button (chosen).** Wrap each input in a `relative` div and absolutely position the icon button on top; keeps shadcn `Input` untouched. Small, local, and reads like a standard password-reveal pattern.
- **Add a `showToggle` prop to the shared `Input` component.** Rejected — `ui/input.tsx` is a 20-line forwardRef over the native element; adding behavioral props would leak Attach-dialog concerns across every `Input` call site and break the project's "compose at the call site" convention.
- **Single "Show values" switch at the top of the dialog.** Rejected — templates can require multiple env vars at once (e.g. API key + org id); revealing all of them to see one value is coarser than needed and worse from a shoulder-surfing angle.

What tipped it: per-key visibility is both the most granular and the cheapest to implement. The absolute-overlay pattern is idiomatic Tailwind in this codebase already (relative/absolute compositions exist in `Sidebar.tsx`, `MessageBubble.tsx`), so there's no novel primitive to justify.

Tests were deliberately skipped. `AdminMCPTemplates` has no existing test file; spinning one up would require mocking `apiFetch`, `useAgents`, and multiple fetch sequences just to assert that clicking an icon flips a `type` attribute. The regression surface is one boolean per key with no side effects, so the cost/benefit favors `npm run build` (tsc + vite passed) plus the manual smoke checklist in the plan. If reveal behavior grows (e.g. timed auto-hide, copy-to-clipboard), that's the point to introduce a test harness for this component.

Assumption to revisit if violated: the current design relies on `AttachDialog` unmounting on close (which it does — `attachTarget` is set back to `null` in `AdminMCPTemplates.tsx:298-304`), so `showValues` resets automatically. If someone later reuses this dialog by keeping it mounted and toggling visibility via a prop, the reset behavior needs to move into an effect.

## Result

Admins can now click the eye icon next to any credential field to verify what they've pasted, then click again to re-mask. Defaults to masked, resets between dialog opens, and keeps `autoComplete="off"` so the browser won't offer to save the values. Template editor UX unchanged. Frontend build passed (`npm run build`, tsc + vite, exit 0). No backend changes, no schema changes, no new dependencies.
