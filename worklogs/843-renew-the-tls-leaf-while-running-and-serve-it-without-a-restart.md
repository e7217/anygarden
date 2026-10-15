# feat(federation): renew the TLS leaf while running and serve it without a restart (#843)

- Commit: `8cd2a48` (8cd2a48)
- Author: Claude Opus 5.5 (Co-Authored-By)
- Date: 2026-10-15
- PR: #843

## Situation

Anygarden federation nodes issue TLS leaf certificates that last 90 days and require renewal 30 days before expiry. Prior to this commit, the only way to put a renewed leaf into service was to restart the process — the running `PeerListener` held an SSL context built at startup that was never refreshed. Nodes configured with a root layout would silently accumulate stale leaves, causing hard-to-diagnose mTLS failures once the old leaf expired.

## Task

- Implement a periodic background task that checks whether the current TLS leaf is due for renewal (every 6 hours) and calls `issue_leaf` when needed.
- After issuing a fresh leaf, reload it into the live `SSLContext` of the running `PeerListener` without stopping or restarting the server — existing connections must be unaffected.
- Expose `certificate_expires_at` on the `GET /identity` federation endpoint so callers can observe the current leaf's lifetime.
- Add a test (`test_renewed_leaf_keeps_working_with_the_peer_without_re_pinning`) covering the re-pin-free renewal flow.
- Constrain work to root-layout nodes only; legacy `peer-cert.pem`/`peer-key.pem` nodes are unaffected.

## Action

- **`anygarden/app.py`**
  - Added `PEER_LEAF_CHECK_SECONDS = 6 * 3600` constant (line 595).
  - Added `_renew_peer_leaf_once(app)` (line 598): checks `leaf_needs_renewal(peer_dir)`, calls `issue_leaf`, then calls `listener.reload_certificate()` on the live `PeerListener`.
  - Added `_renew_peer_leaf_periodically(app)` (line 620): async loop sleeping `PEER_LEAF_CHECK_SECONDS`, calling `_renew_peer_leaf_once`, swallowing non-fatal exceptions with a structured error log.
  - Wired the periodic task into the `lifespan` context (line 570-571): started as an `asyncio.create_task` when `peer_service` is not `None`; cancelled in the `finally` block (line 574-575).
  - Updated `_compose_federation_services` docstring (line 664-665) to reference #843 for leaf renewal.

- **`anygarden/federation/transport.py`** (`PeerListener`)
  - Added `reload_certificate(self)` method (line 110-117): calls `self.context.load_cert_chain(cert_path, key_path)` on the live `SSLContext`. New TLS handshakes pick up the renewed leaf immediately; in-flight connections complete with the old one. Added inline docstring explaining why peers remain compatible (they pin the root, not the leaf).

- **`anygarden/federation/router.py`** (`GET /identity`, line 85-98)
  - Added `certificate_expires_at` field to the response, sourcing from `certificate_expires_at(s.certificate_pem)`. Also added `public_url(request)` for symmetric discoverability.

- **`tests/test_federation_root_pin.py`**
  - Reformatted `_root_pair` helper: expanded multi-arg `db.add(User(...))` and `SimpleNamespace(...)` calls to multi-line form to satisfy ruff line-length rules (no logic change).
  - Added `test_renewed_leaf_keeps_working_with_the_peer_without_re_pinning` (line 446+): creates a `_root_pair`, calls `issue_leaf` to simulate renewal, calls `listener.reload_certificate()`, then makes a federation HTTP request to assert the peer still accepts the connection without re-pinning.

## Decisions

- **`SSLContext.load_cert_chain` over restart**: Python's `ssl.SSLContext` allows reloading a cert chain in-place; new handshakes pick it up while existing connections are unaffected. Alternatives considered:
  - Full listener restart — simpler but causes a brief listen-port gap and kills all in-flight requests.
  - Dual-context with atomic pointer swap — no built-in support in `ssl`; would require a custom protocol wrapper.
  - `load_cert_chain` requires no external locking and is the documented approach for live certificate rotation.

- **6-hour check interval**: Leaves last 90 days; renewal triggers at 30 days before expiry. Checking every 6 hours means at most a 6-hour lag before a due leaf is renewed — well within the 30-day window, and cheap (a single `Path.stat` call when no renewal is needed). A shorter interval would be premature optimisation.

- **Root-layout only**: Legacy layout nodes were issued once and are not set up with a CA chain that can sign new leaves. Attempting renewal without the root key would fail silently or noisily. The guard (`files.kind != "root"`) keeps legacy nodes unaffected. *(Unclear from available context: whether a migration path for legacy layout nodes to opt into root layout is planned.)*

- **Background task vs. startup-only renewal**: Startup-only renewal (already present) handles the case where a node was offline when its leaf expired. The periodic task covers nodes that stay up continuously across a 30-day renewal window without restarts.

## Result

- Running federation nodes on a root layout now rotate their TLS leaf certificate without a service restart.
- The `GET /identity` endpoint exposes `certificate_expires_at` and `public_url`, enabling operators and the UI to surface impending expiry (#842 companion).
- `test_renewed_leaf_keeps_working_with_the_peer_without_re_pinning` passes, confirming that a renewed leaf is accepted by an already-connected peer without re-pinning.
- No changes to wire protocol, pin semantics, or legacy-layout behaviour.
