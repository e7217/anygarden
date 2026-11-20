# feat(chat): give the phone composer a full-width text row while in use (#1060)

- Commit: `e1977d6` (e1977d6f71b93a3dced6905b1c825892c7a675ac)
- Author: Changyong Um
- Date: 2026-11-20T22:19:08+09:00
- PR: #1060

## Situation

On phone-width viewports (< 768 px) the message composer's textarea shared a
single flex row with the `+` and send buttons. With all three elements on one
line the textarea was too narrow to type comfortably, and the layout could
collapse back to the cramped idle state the moment the user tapped the `+` or
send button (taking focus away from the textarea). There was also a secondary
bug: the mention popover was anchored relative to the bottom of the composer
box, so when the textarea moved to its own row the popover rendered off-screen.

## Task

- Move the textarea to its own full-width row when the composer is "in use"
  (focused, has typed text, has attachments, or the `+` menu is open).
- Keep the `+` and send buttons on the row below the textarea in that expanded
  state.
- Prevent button taps from collapsing the expanded layout by keeping focus on
  the textarea.
- Reposition the mention popover so it anchors above the textarea's top edge
  regardless of layout state.
- Desktop layout (≥ 768 px) must remain unchanged (single row, always).
- Two new E2E tests to cover the behaviour at 375 px and 1280 px.

## Action

**`packages/cluster/frontend/src/components/MessageInput.tsx`**

- Added `composerFocused` state (`useState(false)`); set via `onFocus` /
  `onBlur` on the composer box `<div>` (`e.currentTarget.contains` guard stops
  spurious collapses when focus moves between children, `line ~761`).
- Derived `composerExpanded` boolean (`line ~686`) — true when any of
  `composerFocused`, `value.length > 0`, `attachments.length > 0`, or
  `addMenuOpen` is truthy.
- Applied `data-expanded` attribute and `group/composer` Tailwind group to the
  composer box so child elements can reference expansion state via CSS.
- Added `max-md:group-data-[expanded]/composer:order-first
  max-md:group-data-[expanded]/composer:basis-full` to the `<textarea>` class
  (`line ~844`) — this moves the textarea before the buttons and gives it
  `flex-basis: 100%` only on small screens when expanded.
- Added `ml-auto` to the send button so it right-aligns on the action row
  (`line ~852`).
- Added `onMouseDown={e => e.preventDefault()}` to both the `+` toggle button
  (`line ~790`) and the send button (`line ~845`) so clicks do not blur the
  textarea, preventing layout collapse mid-tap.
- Fixed `updateMentionPosition` (`line ~614`): popover now anchors at
  `box.clientHeight − el.offsetTop + 4` (distance from composer box bottom to
  textarea top + gap) rather than `el.offsetHeight + 4`, so it appears above
  the textarea in both single-row and expanded layouts.

**`packages/cluster/frontend/e2e/chat-width.spec.ts`**

- Added `test.describe('phone composer rows')` with two tests:
  1. `'the text takes its own row once the composer is in use at 375px'` —
     verifies idle layout has textarea and send on the same row, then after
     focus the textarea moves above the send button and spans nearly full width.
     Also checks a typed-then-blurred draft keeps the expanded layout.
  2. `'desktop keeps one row while focused'` — verifies 1280 px viewport always
     keeps textarea and send on the same row.

## Decisions

- **Tailwind flex-wrap + `order-first` / `basis-full` over a separate mobile
  layout component**: a pure CSS approach (no JS media-query listener, no
  conditional JSX tree) keeps the render path simple and ties the expanded
  state to a single boolean already tracked for other purposes. Alternatives
  like absolutely positioning the textarea or rendering two separate composer
  trees were rejected because they duplicate state and make keyboard/focus
  management harder.
- **`data-[expanded]` attribute + `group/composer`**: preferred over adding a
  CSS class directly because it makes the state machine explicit in the DOM and
  readable in DevTools, and Tailwind's arbitrary group-data variant keeps the
  CSS co-located with the element that owns the state.
- **`onMouseDown preventDefault` for focus retention**: the standard pattern for
  "click a button without moving focus" in browser UIs; hooks into the natural
  event order (mousedown fires before blur). An alternative — calling
  `textareaRef.current.focus()` in each button's `onClick` — was rejected as it
  causes a brief visible blur/refocus cycle on some mobile browsers.
- **Popover anchor changed from `el.offsetHeight` to `box.clientHeight −
  el.offsetTop`**: the original anchor assumed the textarea was flush with the
  bottom of the composer box. After the layout change that assumption no longer
  holds on phones; the new formula works in both configurations. If `offsetParent`
  is ever `null` (SSR edge case) the code falls back to the old `el.offsetHeight`
  value.

## Result

- On viewports < 768 px, once the composer is focused or holds a draft /
  attachments / open `+` menu, the textarea occupies its own full-width row
  with the `+` and send buttons sitting below it.
- Tapping `+` or send no longer collapses the expanded layout.
- The mention popover renders above the textarea in both phone and desktop
  layouts.
- Two new E2E tests added; desktop single-row behaviour is unchanged per the
  second test.
- No pending items.
