# fix(federation): two-year certificates; an expired one stops federation, not the node (#842)

- Commits: `1caa233`, `1250c3b`, `42b6abe` (squashed in the PR)
- Author: Changyong Um (with Claude)
- Date: 2026-10-15
- Issue: #842 (follow-up: #843)

## Situation

Federation node certificates were valid for 90 days and renewal was manual.
Renewing meant regenerating the pair and having each peer admin re-pin it,
which disables every channel grant for that peer; neither step has a UI.
Once a certificate expired, peers silently stopped connecting, and on the next
restart `start --peer-port` reported the pair as unusable and refused to start
the **whole node**, chat included. While planning, a second bug turned up:
`init-credentials --force` read the node ID from the old certificate with the
validity check on, so renewing an expired pair could give the node a new
identity.

## Task

Close the outage risk until automatic renewal (#843) lands: lengthen the
default validity, warn before expiry, keep the node running with federation
off when the certificate has expired, keep the node ID on renewal, and
document manual renewal.

## Action

- `certificates.py`: `CERTIFICATE_LIFETIME = 730 days`, `EXPIRY_WARNING = 30 days`,
  `inspect_certificate(..., check_validity=False)` for identity-only reads,
  `is_expired`, and `create_credentials(now=, lifetime=)` for tests.
- `credentials_setup.py`: an intact but expired pair raises `CredentialsExpired`
  (carrying the identity and expiry). Corrupt, not-yet-valid, partial and
  unmounted-directory cases are still refused.
- `cli.py`: on `CredentialsExpired`, `start` warns and boots without the peer
  listener. It warns 30 days before expiry. `init-credentials --force` keeps
  the node ID of an expired pair.
- `app.py`: `_federation_listener_enabled` keeps the fail-closed rule for a
  requested listener but skips it when the only reason is expiry. Composition
  records `federation_disabled_reason = "CERTIFICATE_EXPIRED"`, and the node
  APIs answer with that code. `/node/peers` reports each peer's
  `certificate_expires_at`.
- UI: an expired-certificate banner with the renewal command, and expiry
  warnings for this node's card and each trusted peer.
- Docs: two-year validity, expiry behaviour, a manual renewal section in the
  two-node runbook (re-pin and grant APIs), CHANGELOG, requirements FED-13
  and FED-14.

## Result

- A live node started with an expired certificate printed the warning, served
  `/healthz`, opened no peer port, and returned `CERTIFICATE_EXPIRED` from
  `/api/v1/node/peers`. After `init-credentials --force` it kept the same node
  ID, got a two-year certificate, and opened the peer port on restart.
- Tests: cluster 3609 passed / 2 skipped; vitest and the frontend build passed;
  FED-13 and FED-14 pass.
- Still manual: re-pinning and re-granting after renewal (no UI). Automatic
  renewal is #843.
