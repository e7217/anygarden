# fix(ui): shrink the xs avatar presence dot so it does not crowd the 20px avatar (#875)

- Commit: `6cda317` (6cda317a58c04144e100a1a048f48211125522ad)
- Author: Changyong Um
- Date: 2026-10-16T17:24:18+09:00
- PR: #875

## Situation

PR #869 introduced an agent-presence overlay on the `EntityAvatar` component, placing a `PresenceDot` at the top-right corner of the avatar. For the `xs` size (20px avatar), the dot was 8px in diameter with a 2px halo ring, making the dot+ring occupy ~60% of the avatar's width. This left almost no visual breathing room for the engine glyph that sits in the bottom-right corner, making `xs` avatars appear crowded and unbalanced compared to larger sizes where the dot is ~30% of the avatar diameter.

## Task

- Reduce the `xs` presence dot diameter so it occupies roughly 30% of the 20px avatar (matching the proportion used by `sm`/`md`/`lg`).
- Thin the halo ring for `xs` only, so the combined dot+ring footprint stays clearly under half the avatar width.
- Leave `sm`, `md`, and `lg` sizes unchanged.
- Make the ring width configurable via a new `PresenceDot` prop so `EntityAvatar` can pass per-size values without duplicating ring logic.

## Action

- **`packages/cluster/frontend/src/components/PresenceDot.tsx`**
  - Added `ringWidth?: number` prop (default `2`) to `PresenceDotProps` (line ~42).
  - Threaded `ringWidth` into the `boxShadow` calculation for the `ring` branch, replacing the hard-coded `2px` (lines ~99–102).

- **`packages/cluster/frontend/src/components/EntityAvatar.tsx`**
  - Changed `PRESENCE_PX.xs` from `8` to `6` (line ~62).
  - Added `PRESENCE_RING_PX` lookup table: `xs: 1.5`, all others `2` (lines ~70–75).
  - Passed `ringWidth={PRESENCE_RING_PX[size]}` to `<PresenceDot>` in the render (line ~226).

- **`packages/cluster/frontend/src/components/EntityAvatar.test.tsx`**
  - Updated the `xs` presence dot assertion from `8px` to `6px` and added a `boxShadow` check for `0 0 0 1.5px` (lines ~235–237).
  - Enhanced the `lg` test to also assert `boxShadow` contains `0 0 0 2px` (lines ~247–249).

## Decisions

- **6px dot for `xs` (not 7px or keeping 8px)**: 6 ÷ 20 = 30%, aligning with the visual proportion used at `sm` (8 ÷ 28 ≈ 29%) and `md` (10 ÷ 36 ≈ 28%). 7px would have been 35%, still noticeably larger than other sizes. 8px (the old value) was the outlier at 40% raw plus 2px ring on each side.

- **Per-size `ringWidth` prop on `PresenceDot` (not a separate `xsRing` prop or a hardcoded branch inside `PresenceDot`)**: Keeping the ring width as a caller-supplied number keeps `PresenceDot` generic and unaware of the avatar size system. A hardcoded `xs` branch inside `PresenceDot` would have coupled two unrelated concepts. The default of `2` preserves backward compatibility for all other call sites.

- **1.5px ring for `xs` (not 1px)**: 1.5px visually reads as a thin but continuous halo on retina displays; 1px can disappear on low-DPI screens. The offline hollow-ring inset stroke is kept at `1.5px` regardless, so 1.5px outer ring + 1.5px inset stroke are consistent at `xs`.

- **Other sizes unchanged**: The issue was isolated to `xs`; no visual regression at `sm`/`md`/`lg` was reported, and changing them would risk unintended side-effects on other avatar usages.

## Result

- `xs` avatars now display a 6px presence dot with a 1.5px halo, occupying ~30% of the 20px face — matching the proportion of larger sizes and leaving room for the engine glyph.
- All three modified files have updated or new test assertions; no tests were deleted.
- `sm`/`md`/`lg` dot sizes and ring widths are unchanged.
- No pending work noted.
