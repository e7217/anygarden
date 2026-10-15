# fix(federation): two-year peer certificates; an expired one turns federation off, not the node (#842)

- Commit: `1caa233` (1caa233f0d3bb161fa85b381c8011adabad394a0)
- Author: Changyong Um
- Date: 2026-10-15T14:12:11+09:00
- PR: #842

## Situation

Peer certificates had a 90-day lifetime. When a certificate expired, federation silently broke and restarting the node with `--peer-port` would refuse to start at all because `ensure_peer_credentials` treated an expired certificate the same as a corrupt one. Additionally, `init-credentials --force` used `inspect_certificate` (which rejects expired certs) to read the node ID before issuing a new cert, so forcing renewal on an expired pair discarded the node ID and created a new identity — requiring every peer to re-pin from scratch.

## Task

- Extend default certificate lifetime from 90 days to 2 years.
- Distinguish between a structurally broken credential pair (still fail closed) and a valid-but-expired pair (node boots, federation stays off).
- Expose the expiry reason through the API (`CERTIFICATE_EXPIRED` 503) so the admin and UI know what to fix.
- Warn at startup when expiry is within 30 days.
- Fix `init-credentials --force` so it preserves the node ID even when renewing an expired pair.
- Report each peer's certificate expiry in the `GET /node/peers` response.
- Add a full test suite covering the new expiry paths.

## Action

**`packages/cluster/anygarden/federation/certificates.py`**
- Added `CERTIFICATE_LIFETIME = timedelta(days=730)` and `EXPIRY_WARNING = timedelta(days=30)` constants.
- Added `check_validity: bool = True` kwarg to `inspect_certificate`; when `False`, the validity-window check is skipped while structural validation (SAN, EKU, size) still runs.
- Added `is_expired(pem, *, now)` helper.
- Extended `create_credentials` to accept `now` and `lifetime` kwargs (both test-only; production defaults to `datetime.now(UTC)` and `CERTIFICATE_LIFETIME`). Changed hard-coded `days=90` to `lifetime`.

**`packages/cluster/anygarden/federation/credentials_setup.py`**
- Added `CredentialsExpired(CredentialSetupError)` with `identity` and `expired_at` fields.
- In `ensure_peer_credentials`, split the existing catch block: first read the pair with `check_validity=False` to get the identity; if `is_expired`, raise `CredentialsExpired`; then re-validate with `check_validity=True` to catch a not-yet-valid pair (clock skew / file problem).

**`packages/cluster/anygarden/cli.py` (`start_node`)**
- Added a `except CredentialsExpired` branch before `except CredentialSetupError`: prints two WARNING lines and continues without setting `config.peer_listen_port`.
- After a successful `ensure_peer_credentials`, reads the certificate PEM and warns when expiry is within `EXPIRY_WARNING` days.
- Changed the "Created new peer credentials" message from "valid 90 days" to "valid 2 years".

**`packages/cluster/anygarden/cli.py` (`federation_init_credentials`)**
- In the `--force` branch that recovers the node ID from the existing cert, switched to `inspect_certificate(pem, check_validity=False)` so expired certs still yield their node ID.

**`packages/cluster/anygarden/app.py`**
- Extracted `_federation_listener_enabled(app)` from `lifespan`; it returns `False` (with a structured warning log) when `federation_disabled_reason == "CERTIFICATE_EXPIRED"`, raises `RuntimeError` for other compose failures, and falls through to `True` when the service is healthy.
- In `_compose_federation_services`, before logging `peer_credentials.invalid`, checks `is_expired`; if expired, sets `app.state.federation_disabled_reason = "CERTIFICATE_EXPIRED"` and logs `peer_credentials.expired` with the expiry timestamp, then returns without raising.

**`packages/cluster/anygarden/federation/router.py`**
- `service()` dependency now reads `app.state.federation_disabled_reason` and raises `PeerError(reason or "PEERING_DISABLED", 503)`.
- `GET /node/peers` response now includes `"certificate_expires_at"` for each peer row.

**`packages/cluster/tests/test_federation_cert_expiry.py`** (305 lines, new file)
- Unit tests for `inspect_certificate(check_validity=False)`, `is_expired`, `create_credentials` lifetime, `CredentialsExpired` shape.
- CLI tests via `CliRunner` + a monkeypatched `create_app` stub (`_StopBeforeServing`) covering: expired pair boots without peer listener; 30-day warning fires; fresh pair logs nothing; `--force` renewal preserves node ID.
- App/router integration tests: expired credentials set `federation_disabled_reason`; API returns `CERTIFICATE_EXPIRED`; absent credentials keep the generic `PEERING_DISABLED` code; `_federation_listener_enabled` returns `False` for expired, raises for unusable.

## Decisions

**Certificate lifetime: 2 years vs. 90 days vs. 10+ years**
- 90 days: keeps the existing manual-renewal friction (re-pin + re-grant permissions, no UI) every quarter.
- 10+ years (Syncthing model): eliminates renewal work but conflicts with the planned short-lived transport certificates in #843, and removes the habit of key rotation.
- **2 years chosen**: trust anchor is the pinned fingerprint, not certificate expiry — revocation is per-peer, not time-based. A shorter lifetime mostly generated manual re-pin failures without adding real security. 2 years bridges the gap until #843 ships. If #843 is delayed beyond 2 years, this decision should be revisited.

**Expired certificate at startup: fail closed vs. continue federation-off**
- Fail closed (previous behavior): consistent with the #594 principle that an explicitly requested listener must not run silently disabled.
- Keep listener open with expired cert: semantically pointless since peers reject it anyway; misleading.
- **Boot the node, disable only federation, surface the reason**: keeps the #594 spirit (the disabled state is explicit — CLI warning, structured log, `CERTIFICATE_EXPIRED` API code, planned UI banner) while limiting the blast radius to federation. Corrupt/partial pairs still fail closed (config error, not cert expiry).
- Assumption: live-expiry detection (cert expires while the process runs) is out of scope; the 30-day CLI warning is considered sufficient notice for a manual restart cycle.

**`init-credentials --force` node-ID preservation on expired certs**
- Previous code called `inspect_certificate` (validity-checked), so renewal of an expired cert silently minted a new UUID, forcing all peers to re-pin again.
- Fixed by passing `check_validity=False`; this is not a trust decision — the node ID is read purely for identity continuity during renewal, not for authentication.

**Re-pin / re-grant UI**
- Deferred to #843. After renewal peers must `POST /api/v1/node/peers/{id}/certificate` and `PUT .../grants/{channel}` via API; this is documented in the plan but no UI is added here. Building UI now would conflict with the routing changes planned for #843.

## Result

- New certificates last 2 years; existing pairs are unaffected until they are force-renewed.
- A node with an expired peer certificate starts normally; chat and other features are unaffected; `GET /api/v1/node/peers` returns 503 `CERTIFICATE_EXPIRED` instead of the generic `PEERING_DISABLED`.
- `start --peer-port` with an expired cert prints two WARNING lines and the renewal command, then continues without starting the peer listener.
- `start --peer-port` with a cert expiring within 30 days prints a WARNING with the number of days remaining.
- `federation init-credentials --force` on an expired cert preserves the node ID.
- `GET /node/peers` includes `certificate_expires_at` for each connected peer.
- 305 lines of new tests cover all new paths; `@pytest.mark.req` markers added for FED-13 and FED-14.
- Frontend UI changes (expiry banner, per-peer expiry column, i18n strings) are tracked in the plan but were not part of this backend commit.
