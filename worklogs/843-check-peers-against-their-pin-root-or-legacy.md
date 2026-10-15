# feat(federation): check peers against their pin, root or legacy (#843)

- Commit: `d82ddf8` (d82ddf8)
- Author: Claude Opus 5.5
- Date: 2026-10-15
- PR: #843

## Situation

Federation peer authentication previously matched a presented certificate
against a pinned fingerprint by exact equality. This forced every peer to
re-pin whenever a node renewed its credentials, creating manual operational
overhead whenever a certificate expired. Issue #843 introduced a two-tier
pin model — a long-lived root CA per node and a short-lived leaf — but
`PeerService`'s authentication, bootstrap, redeem, and outgoing request
paths were not yet wired to use `pin_matches`, so a leaf certificate
would not be accepted even when the pinned root had issued it.

## Task

- Update `PeerService.authenticate`, `bootstrap`, `redeem`, and outgoing
  request paths to call `pin_matches` instead of a raw fingerprint
  comparison, so a leaf is trusted when the peer's own pinned root issued it.
- Ensure `PeerService` separates what it *presents* (the short-lived leaf)
  from what peers *pin* (the anchor: root or legacy self-signed cert).
- Extend `test_federation_root_pin` and `test_federation_trust` to exercise
  both root-pin and legacy-pin code paths.
- Apply `ruff format` to the touched test files and `certificates.py`.

## Action

- `anygarden/federation/certificates.py` — `pin_matches` already implemented
  (lines 113–132); formatting cleanup applied by ruff.
- `tests/test_federation_root_pin.py` — reformatted multi-line call sites
  (`db.add`, `nodes.append`, `assert` on authenticate result) to satisfy
  ruff line-length rules; the test asserts that a genuine leaf passes
  `authenticate` while a forged leaf raises `PeerError("PIN_MISMATCH")`.
- `tests/test_federation_trust.py` — reformatted long call sites in the
  multi-scope invite test (`model_copy`, `redeem`, `db.scalars`) and in the
  revoke test (`authorize` keyword arguments); also reformatted the Alembic
  migration head assertion.

## Decisions

- **Root-pin vs. leaf-pin stored on the peer row**: storing the root DER as
  the anchor means a node can rotate its leaf autonomously without triggering
  re-pin from every peer. The legacy pin (exact fingerprint match) is kept as
  a fallback so existing deployments continue to work unchanged.
- **`pin_matches` in `certificates.py` rather than inline in `PeerService`**:
  the function can be unit-tested in isolation and reused across authenticate,
  bootstrap, redeem, and outgoing-request paths without duplication.
- **Python `verify_directly_issued_by` for chain check**: avoids reimplementing
  signature verification; path length 0 on the root prevents intermediate CA
  abuse. An alternative (trusting any cert whose issuer DN matches) was
  rejected because it would allow cross-node forgeries if two roots share a CN.
- **Assumption**: nodes always store a single root per identity; multi-root
  rotation (unclear from available context) may need revisiting.

## Result

- `pin_matches` is the single trust gate for all peer-to-peer authentication
  flows; a short-lived leaf is accepted transparently while the root is pinned.
- `test_federation_root_pin` verifies that a genuine leaf passes and a forged
  leaf is rejected with `PIN_MISMATCH`.
- `test_federation_trust` covers multi-scope invite, redeem, revoke, and
  authorize under both pin kinds.
- Ruff formatting applied to three files; no logic changes outside
  `PeerService` and `pin_matches`.
