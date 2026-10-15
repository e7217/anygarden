# fix(federation): show setup errors in their card and keep node cards in the viewport (#839)

- Commit: `98bb037` (98bb0371552cb045cda81fd6b3f2d1bdeda4742a)
- Author: Changyong Um
- Date: 2026-10-15T11:23:41+09:00
- PR: #839

## Situation

The federation nodes tab showed a single `ConnectionError` banner at the top of the setup section after any action (create card, send invite, accept invite). Users could not tell which card produced the error. Additionally, the two-column grid for trusted-peers and invitations cards had no `min-w-0` constraint, so long node IDs would widen the grid tracks and push cards off-screen on narrow viewports.

## Task

- Show each setup action's error inside the card that initiated it, not as a floating banner above all three cards.
- Keep the old top-level error visible only when no card has claimed ownership (e.g. a revoke operation).
- Prevent the node/invitation grid cards from overflowing the viewport when node IDs are long strings.
- Add a test verifying that an error prop renders inside `InviteComposer` and that `onAction` fires before the async operation starts.

## Action

- `FederationConnectionSetup.tsx`:
  - Extracted a `SetupProps` interface (`federation`, `error?: string | null`, `onAction?: () => void`) and applied it to `NodeConnectionCard`, `InviteComposer`, and `InviteAcceptor`.
  - Moved `<ConnectionError error={error} />` inside each card's `CardContent`, just before the result area.
  - Called `onAction?.()` at the start of every async action (`create`, `review`, `submit`) in each card.
  - Removed the `mt-4` margin from `ConnectionError`'s `<p>` so placement inside each card is handled by card layout.

- `FederationLiveWorkspace.tsx`:
  - Added `errorOwner` state (`string | null`) and a `setupErrorProps(owner)` helper that returns `{ error, onAction }` based on which card last started an action.
  - Spread `setupErrorProps('card' | 'invite' | 'accept')` onto the respective setup cards.
  - Retained the global `<ConnectionError>` inside a conditional `div` that only renders when `errorOwner === null` (covers revoke actions that aren't owned by a setup card).
  - Added `setErrorOwner(null)` to the revoke buttons for peers and invitations so a pending setup error clears when a node-level action runs.
  - Added `className="min-w-0"` to the two grid `<Card>` elements (trusted-peers and invitations) to prevent grid-track blowout.

- `FederationConnectionSetup.test.tsx`:
  - Added test `'shows the error of its own action and reports when it starts one'` for `InviteComposer`: renders with `error="ENDPOINT_DENIED (400)"`, asserts `role="alert"` content matches the human-readable message, fires a card paste + Review click, asserts `onAction` was called once, and awaits the peer preview appearing.

## Decisions

- **Error ownership via `errorOwner` state rather than per-card state**: placing the error inside each card avoids a prop-drilling chain for the combined `(error, clearError)` pair. A single `errorOwner` string in the parent is simpler than three separate `error` states and guarantees only one card shows an error at a time.
- **`onAction` callback instead of lifting action results**: the cards already own their async flows; exposing a lightweight `onAction` hook (called before the async op) lets the parent claim ownership without rewriting the cards' internal logic.
- **Global banner preserved for revoke actions**: revoke does not go through a setup card, so `errorOwner === null` still shows `actionError` at the top level. Removing it entirely would silently swallow revoke errors.
- **`min-w-0` on grid cards**: CSS grid items default to `min-width: auto`, which lets content dictate track width. Adding `min-w-0` (equivalent to `min-width: 0`) forces truncation of long strings rather than layout overflow. The fix is a one-token addition with no visual side-effect on normal content.
- Assumption: if future cards are added to the setup section they must also accept `SetupProps` and call `onAction` before their async ops, otherwise the `errorOwner` model will silently skip them.

## Result

- Setup errors now appear inside the card that triggered them, giving users direct context (e.g. `ENDPOINT_DENIED` appears under the "Create connection card" form, not floating above all three cards).
- Revoke errors still surface via the top-level banner.
- The trusted-peers and invitations grid cards no longer overflow the viewport on narrow screens when node IDs are long.
- All 12 FED-* requirement rows pass (FED-01 through FED-12, 4–20 tests each) with `vitest=0`.
- New test coverage for `InviteComposer` error rendering and `onAction` signalling added.
