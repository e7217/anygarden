# Product-app federation wiring (#593 task #34)

Base: main `6a812ca` (includes PR601/603/602 chain and PR605). Architecture
contract approved in task #36 (thread `08af80fa`), including the mid-review
correction: criteria B-3 (rooms API opens no new content path) takes precedence
over applying one visibility predicate to all four `accessible_room_ids`
consumers — confirmed by architect and PM (messages `f5a3542c`..`7b8520a0`).

## Phase A — composition (no gate)

- `create_app` always mounts the node admin router (`mount_admin`) and the
  shared-channel local router. `/api/v1/node/*` answers an explicit
  `PEERING_DISABLED` 503 instead of 404; shared-channel endpoints answer
  `SHARING_DISABLED` 503 when not composed.
- `_startup_server` → `_compose_federation_services(app)`: when both
  `peer-cert.pem`/`peer-key.pem` exist under `config.peer_credentials_dir`
  (default `{local_node_data_dir or ~/.anygarden}/peer`), a `PeerService` is
  constructed from the certificate's node identity and a `ChannelService` is
  attached. An explicit `create_app(channel_service=...)` injection is never
  replaced.
- Identity creation stays the explicit `federation.certificates.create_credentials`
  step: startup never writes, overwrites, or rotates credentials and never
  starts the mTLS listener. Partial credential sets log a warning and leave
  everything disabled rather than crashing boot.
- The node admin router's authentication runs in the product app context with
  the app's JWT settings; the fresh-DB demotion checks are unchanged.

## Phase B — bindings listing + shared-room metadata visibility (gated, approved)

### A. `GET /api/v1/shared-channels/bindings`

Local admin only (`get_admin_identity` + fresh DB role check via
`ChannelService.require_local_admin`). Response is metadata only:
`authority_node_id`, `channel_id`, `local_room_id`, `last_seq`, `applied_seq`.
No invite tokens, certificate material, credentials, or message/submission
bodies. Local JWT surface only.

### B. Shared rooms in rooms API (metadata only)

- Single visibility predicate `shared_channels.visibility.visible_shared_room_ids`:
  local user/agent principal (guests fail closed), plus — authority-side
  bindings — an **active** `SharedParticipant` roster row; mirror-side
  bindings — the current inbound grant must be active, unexpired, and list the
  principal among its actors. Remote principals are never turned into local
  Participants/Users. Evaluated per request inside the caller's transaction;
  no cache; no write locks on this read path.
- `accessible_room_ids(..., include_shared=True, channel_service=...)` is
  passed **only** by the two metadata listings: rooms collection
  (`rooms/router.py` list) and sub-rooms listing. **Saved messages and search
  never pass it** — they are message-content surfaces, and B-3 forbids opening
  a legacy content path for shared channels. A `None` channel service (sharing
  disabled) preserves the pre-#593 full exclusion everywhere.
- **Admin asymmetry (deliberate)**: the global-admin bypass never re-includes
  shared rooms in listings or detail — admin visibility is served by the
  bindings listing (A) plus the existing shared-channel snapshot API
  (`local_access` unchanged). This asymmetry is intentional, not drift.
- Room detail `GET /api/v1/rooms/{id}`: for shared-bound rooms the same
  predicate gates a **metadata-only** read (Room object; non-participants get
  the existence-hiding 404). Every rooms-API sub-resource (messages, threads,
  participants, settings, …) keeps the default 409
  `SHARED_CHANNEL_API_REQUIRED` — no new content path. Archived shared rooms
  reuse the ordinary archived handling; no special branch.

## Verification

- `tests/test_app_federation_wiring.py` (11 tests): mounted-but-disabled 503s,
  composition from real generated credentials (admin 200 on `/api/v1/node/peers`),
  explicit-injection precedence, partial-credential fail-closed; Phase B —
  shared rooms listed for visible participants (authority + mirror), global
  admin bypass exclusion and outsider exclusion, roster tombstone + grant
  revocation hide on the next request (no cache), detail metadata-only with
  sub-resource 409 and non-participant 404, bindings admin-only with exact
  metadata field set, saved/search scopes stay excluded, guest fail-closed.
- Provider-free: real SQLite + real generated credentials, loopback app only;
  no external nodes, providers, deployment, or production systems.
- Cluster regression before Phase B commit: 1,703 passed / 1 deselected.
