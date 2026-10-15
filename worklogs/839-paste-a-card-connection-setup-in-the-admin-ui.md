# feat(federation): paste-a-card connection setup in the admin UI (#839)

- Commit: `ae4542a` (ae4542a57368dcee4145590705d59299b96646ac)
- Author: Changyong Um
- Date: 2026-10-15T11:15:25+09:00
- PR: #839

## Situation

The federation connection flow previously required admins to copy-paste a node ID,
a raw PEM certificate, a bundle JSON object, and an endpoint URL across several manual
fields. With #839's backend introducing connection cards (`agpeer1.`) and invite codes
(`aginvite1.`) as opaque, pasteable strings, the frontend still rendered the old
field-by-field form and had no UI for the new encoded formats. The Connection tab in
the admin workspace lacked any display of this node's own identity card, and the
create/accept invite forms expected raw values that the new API no longer required.

## Task

- Replace the old manual-entry create-invite and accept-invite forms with a unified
  "paste-a-card" setup flow.
- Show this node's own connection card at the top of the Connection tab (for outbound
  sharing), with copy-to-clipboard support.
- Let admins paste the other node's connection card or an invite code to trigger
  server-side inspection and prefill.
- Surface the suggested IPs from the server; require explicit confirmation of private
  IPs and out-of-band fingerprint verification before submit.
- Remove `protocol_version` from `createInvite` calls (the closed `InviteCreate`
  schema rejects it with `INVALID_SCHEMA`).
- Add full i18n catalog entries (EN/KO) for all new copy.

## Action

**`FederationConnectionSetup.tsx`** (new, 490 lines):
- Exports `ConnectionError` — maps backend error codes (`CODE_INVALID`,
  `IDENTITY_MISMATCH`, `CERTIFICATE_DENIED`, `ENDPOINT_DENIED`, etc.) to localised
  messages via `ERROR_KEYS` lookup.
- Exports `Fingerprint` — renders a SHA-256 hex string in space-separated 4-char groups
  for human read-aloud verification.
- `CopyableCode` — copyable `<Textarea>` with clipboard API fallback (select-all for
  plain-HTTP LAN pages without clipboard access).
- `PeerReview` — shared review sub-form: shows node ID, address, fingerprint; editable
  approved-IPs field pre-filled from `suggested_ips`; "allow private" checkbox with
  `requires_private` warning; fingerprint-confirmed checkbox that must be checked to
  submit.
- `MyCard` section — calls `createConnectionCard` to generate this node's card (with
  optional `url` override input) and presents the result in `CopyableCode`.
- `InvitePanel` — paste-a-card flow: admin pastes a connection card, server inspects it
  (`inspectConnectionCard`), renders `PeerReview`, then creates an invite via
  `createInvite`; shows the resulting invite code in `CopyableCode`.
- `AcceptPanel` — paste-invite-code flow: admin pastes the invite code, server inspects
  it (`inspectInviteCode`), renders `PeerReview`, then calls `acceptInvite`.

**`FederationConnectionSetup.test.tsx`** (new, 221 lines):
- Tests for `Fingerprint` grouping, `ConnectionError` key mapping, `CopyableCode`
  fallback path, and `PeerReview` checkbox/field interactions.

**`useFederation.ts`** (modified):
- Added `nodeIdentity: NodeIdentityView | null` state, fetched via `getNodeIdentity()`
  on each `refreshNodes` call (failure silently sets `null`).
- Added `createConnectionCardAction`, `inspectConnectionCardAction`,
  `inspectInviteCodeAction` callbacks exposed from the hook.
- `createInviteAction` return type changed from `InviteBundleView` to
  `InviteCreatedView` (the new backend type that includes `invite_code`).

**`federationApi.ts`** (modified):
- New types: `InviteCreatedView`, `NodeIdentityView`, `ConnectionCardView`,
  `DialPreview`, `ConnectionCardPreview`, `InviteCodePreview`.
- `createInvite`: removed `protocol_version: 1` from the POST body; return type is now
  `InviteCreatedView`.
- New functions: `getNodeIdentity`, `createConnectionCard`, `inspectConnectionCard`,
  `inspectInviteCode`.

**`federationApi.test.ts`** (modified):
- Added tests for `createConnectionCard`, `inspectConnectionCard`, `inspectInviteCode`,
  and `getNodeIdentity` covering success and error paths.

**`federation.ts` (i18n)** (modified):
- Added 28 new copy keys under `federation.connect.*` namespace (EN + KO), covering all
  labels, hints, error codes, and instructional descriptions used by
  `FederationConnectionSetup`.

**`FederationLiveWorkspace.tsx`** (modified):
- Replaced inline legacy invite forms with `<FederationConnectionSetup>` component on
  the Connection tab.

## Decisions

- **Server inspects, client only confirms**: The client never parses the binary formats
  (`agpeer1.` / `aginvite1.`) — it posts them to `/inspect` endpoints and renders what
  the server returns. This keeps decode logic in one place and avoids shipping a second
  base64url+JSON decoder in the browser bundle.
- **`suggested_ips` are prefill, not approval**: The server resolves DNS and returns
  candidate IPs, but the admin must confirm them (and explicitly check "allow private"
  for RFC-1918 addresses). This mirrors the backend's `EndpointInput.approved_ips`
  gate and makes the security boundary visible.
- **Fingerprint confirmation checkbox is mandatory**: Rather than hiding the fingerprint
  or making it advisory, the submit button stays disabled until the admin checks the
  out-of-band confirmation box. This enforces the TOFU step the protocol relies on.
- **Clipboard API fallback via `select()`**: Plain-HTTP LAN deployments lack
  `navigator.clipboard`; the copy button falls back to selecting the textarea so the
  admin can use Ctrl+C. Alternative (Flash/execCommand) was rejected as deprecated.
- **`protocol_version` removed from `createInvite`**: The `InviteCreate` schema is
  closed (no extra fields). Sending `protocol_version: 1` caused `INVALID_SCHEMA` 400s.
  The field was a leftover from an earlier draft and carries no semantic weight in the
  current API.

## Result

- Admins can connect two nodes by sharing a single opaque string each direction (card →
  inspect → invite → inspect → accept), with no manual field entry.
- This node's connection card is visible on the Connection tab immediately on load.
- Private-IP deployments are explicitly opt-in at connection time rather than silently
  failing.
- Fingerprint confirmation step is enforced in the UI before any invite is created or
  accepted.
- `INVALID_SCHEMA` errors from `createInvite` are resolved.
- 221 lines of component tests added; all existing federation API tests updated to
  reflect the new return types.
