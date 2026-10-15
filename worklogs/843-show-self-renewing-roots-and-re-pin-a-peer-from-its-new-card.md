# feat(federation): show self-renewing roots and re-pin a peer from its new card (#843)

- Commit: `8613ce3` (8613ce3f79328061ab82b011ae93adfd515274ac)
- Author: Changyong Um
- Date: 2026-10-15T14:45:30+09:00
- PR: #843

## Situation

The federation UI had no distinction between a node that holds a legacy
certificate pin (renewed manually) and one that holds a root pin (issues
and renews its own TLS leaf automatically). When a peer renewed its root
and sent a new connection card, there was no UI path to re-pin the new
certificate without resorting to the raw `curl` command documented in the
runbook. Admins also had no in-app indication that a node was still on the
legacy path or that their own node's TLS leaf would renew itself.

## Task

- Surface `pin_kind` (`'root'` | `'certificate'`) and `leaf_expires_at`
  from `NodeIdentityView` in the connection-card panel.
- Show a "Renewed automatically · expires {{date}}" notice when
  `pin_kind === 'root'` and `leaf_expires_at` is present.
- Show a migration hint when `pin_kind === 'certificate'` (legacy path).
- Add a **Replace certificate** inline form (`PeerCertificateRotation`) on
  every peer row in the trusted-peers list so admins can re-pin a peer
  after it rotates its root.
- Guard: the form must parse the new card, show its fingerprint for manual
  comparison, warn that channel grants and publication consents will be
  reset, require explicit confirmation, and reject a card that belongs to a
  different node.
- Wire `rotatePeerCertificate` through `useFederation` → `federationApi`.
- Add i18n strings for all new copy (EN + KO).
- Cover the new flows with Vitest/RTL tests (4 new cases).

## Action

- `packages/cluster/frontend/src/lib/federationApi.ts` — extended
  `NodeIdentityView` with `pin_kind?: 'root' | 'certificate'` and
  `leaf_expires_at?: string | null`. Added `rotateCertificate` API call
  (wraps `POST /api/v1/node/peers/:id/certificate`).
- `packages/cluster/frontend/src/hooks/useFederation.ts` — added
  `rotatePeerCertificateAction` callback (calls `rotateCertificate`, then
  `refreshNodes`); exported as `rotatePeerCertificate` in the hook return
  value.
- `packages/cluster/frontend/src/components/FederationConnectionSetup.tsx`
  — in `NodeConnectionCard`:
    - Added conditional `<dt>`/`<dd>` block for TLS leaf auto-renewal when
      `pin_kind === 'root'` (line ~281).
    - Added legacy-pin hint paragraph when `pin_kind === 'certificate'`.
  - New exported component `PeerCertificateRotation`: textarea → Review
    (calls `inspectConnectionCard`) → fingerprint display + warning →
    checkbox confirmation → "Re-pin this node" submit (calls
    `rotatePeerCertificate`). Renders an `role="alert"` error paragraph
    when the card belongs to a different node.
- `packages/cluster/frontend/src/components/FederationLiveWorkspace.tsx`
  — added `rotating` state; each peer row is now a two-div structure
    (header + collapsible `PeerCertificateRotation`). A "Replace
    certificate" outline button toggles the form.
- `packages/cluster/frontend/src/i18n/catalogs/federation.ts` — 8 new
  i18n keys (`federation.cert.leaf`, `.leafAutoRenewed`, `.legacyHint`,
  `.rotate`, `.rotateCard`, `.rotateOtherNode`, `.rotateWarning`,
  `.rotateSubmit`) with EN and KO.
- `FederationConnectionSetup.test.tsx` — 4 new tests under
  `describe('root pins (#843)')`:
    - root node shows "Renewed automatically"
    - legacy pin shows the migration hint
    - `[FED-17]` full re-pin flow with card parse, fingerprint review,
      confirmation checkbox, and submit
    - rejects a card from a different node

## Decisions

- **Inline form per peer row, not a modal**: avoids a secondary routing
  layer and keeps the action close to the data it affects. The existing
  pattern (e.g. invite acceptor) is also inline. A modal would need extra
  focus-management plumbing.
- **Confirmation checkbox before submit** (not just a button): the
  runbook made fingerprint verification a manual step (`cI`); the checkbox
  encodes that step so it cannot be skipped in the UI. Two-step flows
  (Review → confirm → submit) were already established by `InviteAcceptor`.
- **Warn about grant reset before submit, not after**: the API resets
  channel grants conservatively on re-pin (#843 design decision from
  runbook lines 184–187). Surfacing this before confirmation rather than
  as a post-action toast prevents surprised admins.
- **`pin_kind` as an optional field**: `NodeIdentityView` was already
  shipped; making the field required would be a breaking change for older
  server versions. Existing nodes without the field continue to render
  without the new UI elements.

## Result

- Admins can see at a glance whether their node and each peer uses a
  self-renewing root or the legacy manual-renewal path.
- The "Replace certificate" form replaces the raw `curl` workaround in
  the runbook for the common re-pin case (the runbook note at line 174
  "There is no UI for this yet" is now superseded).
- 4 Vitest/RTL tests pass covering the new rendering paths and the
  full re-pin interaction, including the guard against cross-node cards.
- Channel-grant and publication-consent reset is still required after
  re-pinning (server behaviour, not changed here); the warning in the UI
  now makes this expectation explicit before the admin confirms.
