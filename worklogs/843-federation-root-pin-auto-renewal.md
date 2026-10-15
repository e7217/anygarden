# feat(federation): pin a per-node root and renew the TLS leaf automatically (#843)

- Commits: `34a2d13`, `d82ddf8`, `722dc9c`, `8cd2a48`, `8613ce3`, `f38a04b` and follow-ups (squashed in the PR)
- Author: Changyong Um (with Claude)
- Date: 2026-10-15
- Issue: #843 (preceded by #842, #839)

## Situation

Peers pinned the exact TLS certificate of a node: TLS trusted only that
self-signed certificate and the app compared its DER fingerprint. Any renewal
changed the fingerprint, so every peer admin had to re-pin, which also
disables that peer's channel grants. Re-pinning had no UI. #842 lengthened the
certificate lifetime to two years, but renewal stayed fully manual.

## Task

Make renewal a local operation: peers pin a long-lived per-node root, and
the node re-issues its own short-lived TLS leaf. Constraints:

- Python 3.12 `ssl` cannot report which trust anchor verified a chain;
- legacy self-signed pins must keep working;
- a trusted node's root must not be able to impersonate another node.

## Action

- Step 0 spike: a root-anchored handshake works with the existing TLS
  settings. Reloading `load_cert_chain` on a live server context serves the
  new leaf to new handshakes. Root anchors and legacy self-signed anchors can
  share one context. A leaf from an untrusted root is rejected by the server.
- `certificates.py`: `create_root` (CA, pathLen 0, keyCertSign, 10 years) and
  `issue_leaf` (90 days, certificate and key in one file swapped atomically).
  `inspect_certificate` accepts roots (`root=True`, DER kept).
  `pin_matches(pin, presented)` accepts an exact match, or a leaf for the
  same node that the pinned root signed directly. `identity_from_tls` rejects
  a presented root. Root and leaf subjects differ: with equal subjects,
  OpenSSL treated the leaf as self-signed and the real-TLS tests failed.
- `service.py` / `transport.py`: `authenticate`, `bootstrap`, `redeem` and
  outgoing `post` use `pin_matches`. Invite lookups compare the stored pin,
  not the leaf fingerprint. `PeerService` separates what it presents (leaf)
  from what peers pin (`anchor_path`).
- `credentials_setup.py`: `locate_credentials` reads the root or legacy
  layout. A first run creates a root layout. `start` and app startup renew a
  due leaf. `init-credentials` creates a root; `--force` replaces it, keeps
  the node ID, and moves a legacy pair aside as `*.legacy`.
- `app.py`: a six-hourly task renews a due leaf, and
  `PeerListener.reload_certificate` serves it without a restart.
- API and UI: `/node/identity` reports `pin_kind` and `leaf_expires_at`. The
  node card shows automatic renewal, or a hint to switch a legacy pin.
  **Replace certificate** re-pins a peer from its new card, with a fingerprint
  check and a warning that grants are disabled.
- The trust test suite now runs against both pin kinds. New tests cover
  forgery (FED-16), live renewal without a re-pin (FED-15), and legacy
  coexistence and re-pinning (FED-17). Runbooks and CHANGELOG updated.

## Result

- Live: two root-layout nodes on 192.168.100.81 peered using a card and an
  invite code over real mTLS; beta pinned alpha's root. Alpha's leaf was
  replaced and the node restarted. Beta, unchanged (same pin, epoch 1),
  completed `hello` against the new leaf.
- Tests: cluster 3771 passed / 2 skipped; vitest and the frontend build
  passed; FED-10 to FED-17 all pass.
- Noted, not changed: the federation node ID created on a first `start` is
  not the local `node-identity.json` node ID. Nothing couples the two today,
  and pre-creating `node-identity.json` would trip the ownership recovery
  check.
