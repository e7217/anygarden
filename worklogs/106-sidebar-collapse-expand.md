# feat(sidebar): collapse/expand sidebar on desktop (#106)

- Commit: `049fd12` (049fd120f2c63aa9c82dfdcb0f2b45b486bd1b09)
- Author: Changyong Um
- Date: 2026-04-18T17:17:27+09:00
- PR: #106

## Situation

The desktop sidebar is hard-coded to `md:static md:translate-x-0` with a fixed `w-64`, so it always occupies ~256 px of the viewport regardless of what the user is doing on the main pane. That bites on `topology` (node graph wants the full width), on admin machines rows (tables are already cramped at `md`), and whenever someone drops a long paste into MessageInput. Mobile already has an off-canvas drawer via the `open` prop + RoomHeader hamburger, but desktop had no escape hatch — nothing short of browser devtools DOM editing could reclaim the column. Sibling issue #105 was adding a sidebar action menu in parallel; both edits landed in `Sidebar.tsx` + `ChatPage.tsx`, so the implementations were worktree-isolated to avoid accidental clobbering.

## Task

- Give desktop (`md+`) users a way to collapse the sidebar and reclaim the column, without regressing the mobile off-canvas flow.
- Provide three symmetrical entry points so the feature is discoverable regardless of where the user's attention is: a trigger in the sidebar header itself (easy to find while the sidebar is open), a floating button on the main pane (the only visible control once it's collapsed), and a keyboard shortcut for power users.
- Persist the state across reloads — a user who prefers the wider pane shouldn't have to re-collapse on every visit.
- Leave the mobile path completely alone: no re-keying the `open` drawer logic, no `useMediaQuery` shim to force-reset the state at small widths.
- Keep the change local to `Sidebar.tsx` + `ChatPage.tsx` (and a new focused test file) so merging into the sibling #105 branch stays mechanical.

## Action

Frontend (`packages/cluster/frontend/src`):
- `pages/ChatPage.tsx:67` — new `sidebarCollapsed` state with a localStorage-backed initializer (`doorae_sidebar_collapsed`, try/catch guarded for SSR / private-mode parity). `toggleSidebarCollapsed` callback writes the next value back before returning, so the mirror to storage happens even if the consumer doesn't re-render synchronously.
- `pages/ChatPage.tsx:122` — new `Cmd/Ctrl+B` `useEffect` listener that mirrors the existing `Cmd+K` search handler directly above it. Preventing default on the combo keeps the shortcut from leaking into any future rich-text bold affordance, but the immediate motivation is consistency with VS Code muscle memory.
- `pages/ChatPage.tsx:388` — when `sidebarCollapsed` is true, render a `hidden md:inline-flex fixed left-2 top-2 z-30` floating button with a `PanelLeftOpen` glyph. `z-30` sits below the sidebar's `z-40` so an animating sidebar overlays the button naturally during the 200 ms transition. Styled with the existing `shadow-whisper` + `border-[var(--color-border)]` tokens so it reads as a first-class control without a new palette.
- `components/Sidebar.tsx:132` — `SidebarProps` gains `collapsed?: boolean` and `onToggleCollapsed?: () => void`, both optional so the component still renders cleanly if a future caller doesn't care about desktop collapse.
- `components/Sidebar.tsx:339` — the `<aside>` class branch now lives inside the same `transform transition-all` block (bumped from `transition-transform` so width animates too). The collapsed branch applies `md:-translate-x-full md:w-0 md:overflow-hidden md:border-r-0`; the expanded branch restores the original `md:static md:z-auto md:translate-x-0 md:w-64`. Added `aria-hidden={collapsed || undefined}` so assistive tech skips the off-screen tree.
- `components/Sidebar.tsx:354` — new desktop-only `PanelLeftClose` button rendered next to (and conditionally before) the existing mobile `X` close button. Wraps both in a flex container so the header still reads as a single row on any viewport.
- `components/Sidebar.test.tsx` — new suite, 4 tests under jsdom. Stubs `useAuth` / `useAgents` / `useRooms` / `EntityAvatar` (the last to avoid the `@lobehub/ui` bundle bleed) and asserts: default layout keeps `md:w-64`/`md:static`, `collapsed=true` applies `md:-translate-x-full md:w-0 md:overflow-hidden` + `aria-hidden="true"`, the trigger only renders when `onToggleCollapsed` is supplied, and clicking it invokes the callback once.

## Decisions

Rationale was pre-written in `.tmp/plan-106-sidebar-collapse-expand.md`; the decisive threads:

- **`translate-x-full` vs `translate-x-full + w-0` for the collapsed branch.** Picked the dual class because the desktop sidebar sits in a flex row as the first column — translating alone leaves a phantom `256 px` gap that the main pane can't reclaim. Adding `md:w-0` + `md:overflow-hidden` makes flex recompute and expand `min-w-0 flex-1` into the freed space, which is the whole point of the feature. `display: none` would achieve the same layout result but drops the 200 ms animation (CSS `display` isn't transitionable).
- **Ownership in ChatPage vs inside Sidebar.** Placed the state at ChatPage level next to `sidebarOpen` because the floating expand button has to render in the main-content region (i.e. outside the sidebar's subtree) and the keyboard shortcut lives alongside `Cmd+K`. Pushing it down would have forced a ref or a context just so two side effects could read/write the flag.
- **CSS `md:` branch vs JS viewport detection.** Chose pure CSS — if a user collapses on desktop and resizes down below `md`, the stored `collapsed=true` becomes a no-op (mobile uses `open` instead) and the off-canvas drawer behaves exactly as before. When they size back up, their collapsed preference is restored without any `useMediaQuery` round-trip. The alternative (force `collapsed=false` on resize down) would pollute the persisted value on every viewport change.
- **`Cmd/Ctrl+B` vs `Cmd+\` vs `Cmd+/`.** `Cmd+B` matches VS Code muscle memory. Doorae's MessageInput is plain text today so the bold conflict is theoretical; documented as a revisit point if rich-text editing lands (`Cmd+\` is the planned fallback).
- **`aria-hidden` vs `inert` on the collapsed aside.** Stayed with `aria-hidden` because React 19's `inert` prop coverage is still uneven under jsdom and the test assertions needed a deterministic attribute. Focus escape is mitigated by the fact that the whole subtree is translated off-screen (`md:-translate-x-full`) — tab-trap hazards only materialise if the user scripts focus into the hidden region, which no current flow does. Noted for a follow-up if it surfaces.
- **Stubbing `EntityAvatar` in the new test.** Mirrors the established pattern in `MessageBubble.test.tsx`. The component itself is covered by 20 tests in `EntityAvatar.test.tsx`; here we only needed the sidebar to render without dragging `@lobehub/ui` into the unit test runtime.

## Result

- Desktop users can collapse/expand the sidebar from three entry points; persistence survives reloads. Manual verification: open sidebar → click `PanelLeftClose` in header → sidebar slides off, main pane widens, floating `PanelLeftOpen` button appears in the top-left → click it (or `Cmd+B`) → sidebar restores with the same scroll position and expanded-project state.
- Mobile off-canvas unchanged: the `md:`-scoped collapse classes are a no-op below 768 px, so the hamburger → drawer → backdrop → close flow still works byte-for-byte.
- Tests: frontend 177 passing (+4 new in `Sidebar.test.tsx`). `npm run build` green (tsc + vite); bundle size unchanged modulo the two new lucide icons.
- Out of scope, tracked for follow-ups: per-width persistence (currently the same key applies to all desktop widths), `prefers-reduced-motion` handling for the 200 ms width transition, `inert` migration, and the `Cmd+\` fallback if MessageInput ever grows bold formatting.
