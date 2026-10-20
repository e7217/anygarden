# fix(sidebar): keep the responding indicators breathing under reduced motion (#921)

- Commit: `de99ee1` (de99ee1cabbb22f6ee3d31f777dece0876df34a6)
- Author: Changyong Um
- Date: 2026-10-20T09:39:44+09:00
- PR: #921

## Situation

The sidebar's "responding" indicators from #912 (the `···` typing bubble on a room row and the working ring on an agent avatar) froze completely when the OS asked for reduced motion (Windows animation effects off, remote desktops, VMs). A user on such a machine saw a static badge and read the busy signal as stuck.

## Task

- Keep the indicators visibly alive under `prefers-reduced-motion: reduce` without travel or growth.
- Leave the default-motion animations unchanged.
- Record the rule in DESIGN.md so later busy signals follow it.

## Action

- **`packages/cluster/frontend/src/index.css`** (modified): added `responding-breathe` (opacity 0.4↔1) and `presence-working-breathe` (ring fixed at halo + 1.5px, colour 35%↔100%) keyframes. The reduced-motion block now runs them on a 2s cycle, forces the dots' inline step delay to `0s` so they breathe together, and drops the `!important` static box-shadow that would have masked any animation.
- **`DESIGN.md`** (modified): the motion sentence now says busy signals are not decoration and keep a slow in-place turn or fade under reduced motion.
- **`packages/cluster/frontend/e2e/sidebar-responding-motion.spec.ts`** (new): stubs an answering agent DM and checks the computed `animation-name`/`animation-delay` with reduced motion on and off.

## Decisions

- **Fade, not a slowed ripple**: the ring's pulse is an expanding motion, so slowing it (the spinner treatment) still leaves growth on screen. An opacity-only fade keeps the busy cue and fits the reduced-motion intent, the same way the dialog fallback keeps its fade but drops translation.
- **Dots breathe together**: a stepped sequence still reads as left-to-right travel.
- **Animate the ring colour, not the whole dot**: fading the dot would make online/offline presence look like it flickers.
- **No bigger default motion**: several rows can be answering at once, and a stronger effect would get noisy.

## Result

- New e2e: 2 passed (reduced → `responding-breathe`/`presence-working-breathe`, default → unchanged). It failed with `none` before the CSS change.
- `vitest` RespondingIndicator/PresenceDot/Sidebar: 32 passed; `npm run build` OK.
- Screenshots at the low and high phases in light and dark: the ring keeps its size and only its opacity changes.
