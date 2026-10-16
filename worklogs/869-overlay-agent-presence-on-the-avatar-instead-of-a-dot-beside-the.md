# feat(ui): overlay agent presence on the avatar instead of a dot beside the name (#869)

- Commit: `b59d2a0` (b59d2a0fad8d3e5d4e68f543aba94de4e38a4a46)
- Author: Changyong Um
- Date: 2026-10-16T16:31:37+09:00
- PR: #869

## Situation

Agent rows in the sidebar, participant popover, and room agent manager each rendered a standalone `<PresenceDot>` as a sibling to the agent name. This consumed horizontal row space and gave the avatar no semantic connection to the liveness indicator. The `EntityAvatar` component intentionally omitted presence ("callers that already render `<PresenceDot/>` simply place it adjacent"), leaving it up to each callsite to align the dot independently.

## Task

- Add an opt-in `presence` prop to `EntityAvatar` that pins a `PresenceDot` to the top-right corner of the avatar (bottom-right stays reserved for the engine glyph).
- The overlay dot must visually "cut out" of whatever surface the avatar sits on, so it reads cleanly against both selected and hover states.
- Offline state must not rely on color alone (accessibility constraint).
- Move the three callsites (Sidebar agent rows, `ParticipantListPopover`, `ManageRoomAgentsDialog`) to the overlay. Leave inline dots that serve as a color legend for a text state label unchanged (`OverviewPanel`, `AdminMachines`, `AgentSettingsDialog` title).

## Action

- **`PresenceDot.tsx`**: Added `ring?: boolean` and `data-testid` props. When `ring` is true, offline renders hollow (background inherits `--presence-ring`) with an inset ring border so the state is distinguishable without color. Online gets a `box-shadow` halo using `--presence-ring`. Added `data-presence` attribute for test selection.
- **`EntityAvatar.tsx`**: Imported `PresenceDot` and `PresenceDotProps`. Added `presence` prop typed as `Pick<PresenceDotProps, 'online' | 'lastSeenAt' | 'variant' | 'agentState'>`. Added `PRESENCE_PX` size map (`xs/sm: 8px`, `md: 10px`, `lg: 12px`). Renders `<PresenceDot ... ring className="absolute -right-0.5 -top-0.5">` when `presence` is provided. Updated JSDoc to reflect the new overlay design.
- **`Sidebar.tsx`**: Replaced standalone `<PresenceDot>` beside the `EntityAvatar` in `AgentDMListAdmin` with `presence={…}` on the avatar. Added `[--presence-ring:…]` Tailwind variants on the row `div` to track hover and selection background states.
- **`ParticipantListPopover.tsx`**: Removed `<PresenceDot>` sibling; moved `online`/`lastSeenAt` into `presence={…}` on `EntityAvatar`. Added `[--presence-ring:var(--color-surface-elevated)]` to the container. Updated comment from "renders a `<PresenceDot>`" to "overlays a presence dot".
- **`ManageRoomAgentsDialog.tsx`**: Replaced generic `<Bot>` icon + standalone `<PresenceDot>` with `<EntityAvatar … presence={…} />` for both enrolled and available agent lists. Added `[--presence-ring:var(--color-surface-alt)]` to each `<li>`. Removed `Bot` from the lucide import.
- **Tests**: Added / extended tests in `EntityAvatar.test.tsx`, `PresenceDot.test.tsx`, `Sidebar.test.tsx`, `ParticipantListPopover.test.tsx`, and `ManageRoomAgentsDialog.test.tsx` (281 insertions across 10 files).

## Decisions

- **Overlay on avatar vs. dot beside name**: Pinning to the avatar corner reclaims row width, co-locates liveness with the identity glyph (not the label), and removes each callsite's responsibility to align two independent elements. The top-right corner was chosen because bottom-right is already reserved for the engine glyph.
- **`--presence-ring` CSS custom property**: Rather than hardcoding the halo color or threading a prop through the component tree, a CSS variable lets each surface declare its own background. This keeps the component stateless w.r.t. theme and avoids a complex context or prop-drilling chain. Assumption: callers will always set `--presence-ring` on the nearest ancestor when using `ring=true`; if a callsite omits it, the fallback `var(--color-surface)` may produce a visible mismatch.
- **Hollow offline rendering instead of grey fill**: A grey offline dot on a grey surface can be imperceptible; a hollow ring with a border retains shape even when foreground and background colors are similar, satisfying the "not carried by color alone" constraint from the commit body.
- **Keeping inline legend dots unchanged**: Dots in `OverviewPanel`, `AdminMachines`, and `AgentSettingsDialog` serve as color-coded legends paired with a text label — replacing them with avatar overlays would break that pairing. The commit body explicitly scopes the overlay migration to avatar rows only.

## Result

- Agent liveness in the sidebar, participant popover, and room agent manager is now rendered as a corner overlay on the avatar instead of a freestanding dot beside the name, saving row width and co-locating the signal with the identity element.
- Offline state renders hollow so the distinction is accessible without relying on color.
- 10 files changed; 281 lines added, 43 removed. No behavioral changes to surfaces that were not migrated.
- All affected components have new or extended tests.
