# fix(sidebar): keep the responding indicators breathing under reduced motion (#921)

- Commit: `0caeaf0` (0caeaf0edf6059be00a62de525e0a5771756a800)
- Author: Changyong Um
- Date: 2026-10-20T09:38:47+09:00
- PR: #921

## Situation

The sidebar's "responding" indicators — the three-dot typing bubble (`.responding-dot`) and the working presence ring (`.presence-working`) — froze completely when the OS/browser had `prefers-reduced-motion: reduce` enabled. The `@media (prefers-reduced-motion: reduce)` block set both to `animation: none` and hardcoded a static `box-shadow` with `!important` on the ring, turning a live busy signal into a badge that looked like the agent had stopped. There was no way for a user with motion sensitivity to tell whether an agent was still thinking.

## Task

- Keep the responding indicators visually alive under reduced motion — they must read as a busy signal, not a static badge.
- Remove all travel and growth from the animation (no positional shift, no scale change).
- Make the three dots breathe in unison (same phase, no staggered step delay) so there is no perceived motion across the row.
- Drop the `!important` override on the ring's `box-shadow` that would have masked any animation.
- Document the busy-signal rule in `DESIGN.md` so future contributors know the constraint.
- Add Playwright E2E tests covering both the reduced-motion and default-motion paths.

## Action

- **`packages/cluster/frontend/src/index.css`**:
  - Added `@keyframes responding-breathe` (opacity 0.4→1→0.4, 2s) — an in-place fade with no positional change.
  - Added `@keyframes presence-working-breathe` — alternates the `box-shadow` halo between 35% alpha and full opacity, keeping the ring's size fixed.
  - Inside `@media (prefers-reduced-motion: reduce)`, replaced `animation: none; opacity: 1` on `.responding-dot` with `animation: responding-breathe 2s ease-in-out infinite; animation-delay: 0s !important` (overrides the per-dot inline step delay so dots breathe together).
  - Replaced the static `animation: none; box-shadow: … !important` on `.presence-working` with `animation: presence-working-breathe 2s ease-in-out infinite` (removed the `!important` ring).
  - Updated the comment block above the `responding-dot` keyframes to mention `#921`.
- **`packages/cluster/frontend/e2e/sidebar-responding-motion.spec.ts`** (new, 82 lines): Playwright E2E suite tagged `MSG-09 (#921)`. Two tests: `reduced motion keeps the responding indicators breathing in place` — stubs the API, emulates `reducedMotion: 'reduce'`, asserts that all three `.responding-dot` elements carry `animationName: responding-breathe` with `animationDelay: 0s`, and that `.presence-working` elements carry `animationName: presence-working-breathe`; `default motion keeps the stepping dots and the growing ring` — emulates `no-preference`, asserts original animation names and staggered delays (`0s`, `0.16s`, `0.32s`) are intact.
- **`DESIGN.md`**: Appended to the accessibility paragraph: "Busy signals (spinners, responding indicators) are not decoration: under reduced motion they keep a slow in-place turn or fade rather than stopping."

## Decisions

- **Breathe rather than stop**: The `prefers-reduced-motion` spec says to reduce *unnecessary* motion; it does not require removing all animation. A spinner or busy indicator is functional: removing it hides program state. The slow opacity fade satisfies both the accessibility intent (no travel, no growth) and the UX requirement (state remains readable). The alternative — keeping `animation: none` and relying on a static visual treatment — was rejected because users with motion sensitivity could no longer tell whether the agent was still working.
- **All dots breathe in phase (`animation-delay: 0s !important`)**: Under default motion the three dots step across with 0 / 0.16s / 0.32s delays, creating apparent left-to-right movement. That movement is exactly what reduced-motion users want to avoid. Collapsing all delays to 0s via `!important` (overriding the inline `style` attribute set by the component) keeps the dots on the same phase without changing the component's JS. Alternative (patching the component to conditionally omit the delays) was considered but rejected as cross-cutting; a CSS-only fix is fully contained and requires no component changes.
- **`box-shadow` fade for the ring** (no `transform` or size change): The original `presence-working` animation grows the ring outward via `box-shadow` spread. Reduced-motion variant fixes the spread at `halo-width + 1.5px` and fades alpha only, so the ring's footprint never changes. The earlier `!important` static override was dropped because it would have blocked the new breathe animation.
- **Document in `DESIGN.md`**: The distinction between decorative and functional animation was implicit. Adding one sentence to the accessibility section makes the rule durable and avoids the same mistake recurring in future indicator components.

## Result

- Under `prefers-reduced-motion: reduce`: `.responding-dot` elements animate with `responding-breathe` at identical phase; `.presence-working` animates with `presence-working-breathe`. No travel, no growth. The busy signal remains legible.
- Under default motion: behavior unchanged — stepping dots with staggered delays and the growing ring.
- Two new Playwright E2E tests cover both paths. The reduced-motion test verifies `animationName` and `animationDelay` computed properties directly.
- `DESIGN.md` updated with the busy-signal accessibility rule.
