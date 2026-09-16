# Peer trust and invitation control plane (#590)

This implementation follows ADR 007 from PR #596 at
`ac7667718f26a78e4a4b686011ee456ab074c9b5` (wire schemas unchanged from
`7ff28be1709f037e6e549df3946deef4ea617fc2`). It is based on main
`66235f332f6538bceb1591f1c8b12bde4dbbb7ef`. The contract files remain owned by
#587 and are not modified here. The principal definition is copied verbatim into
a test fixture with its source SHA and JSON pointer; examples exercise both
`human` and `agent` (the original scenario corpus covered only `agent`).

## Integration boundaries

- `PeerService(node_id, cert_path, key_path, sessions, local_policy=None)` receives
  the immutable UUID from #588's `state.node_owner.identity.node_id`. It does not
  create, overwrite or derive that UUID from a URL/Machine/name.
- `mount_admin(app, peer_service=None)` registers only the local JWT-admin API.
  All methods also verify the administrator's current database role. Without
  explicit service injection, peering is disabled (503). #588 owns app wiring.
- `PeerListener(service, host, port)` is an opt-in, separate TLS listener mounting
  only the peer control router. `await start()`/`await stop()` manage it. The
  ordinary app does not accept remote TLS identity via headers or admin JWTs.
- `create_credentials(directory, node_id)` is explicit local setup, not startup
  migration. It creates a 90-day P-256 certificate and a mode-0600 private key,
  never overwrites existing credentials, and includes `urn:anygarden:node:UUID`
  in its URI SAN. Operators exchange public certificates and exact fingerprints
  out of band. Private keys and runtime credentials never appear in invitations.
- TLS contexts require client/server certificate verification and trust only
  locally approved certificates, with exact leaf fingerprint and node URI
  checks before sending a body. Hostname identity is intentionally replaced by
  the immutable node URI and leaf pin. They do not load public/system CAs,
  environment proxies, or SSL key-log settings. See [Python ssl](https://docs.python.org/3/library/ssl.html).
- The listener's Uvicorn H11 adapter obtains the certificate from the verified
  SSL transport and places a server-owned `CertificateIdentity` in ASGI scope.
  An ordinary Uvicorn/proxy deployment lacking that adapter fails closed (401).
  TLS-terminating proxy headers are not supported.

Actual unified CLI enablement/configuration remains a #588 integration change.
This PR tests the product listeners on loopback; it does not claim deployed
peering, shared message/task E2E, or runtime execution cancellation.

## HTTP contract

Local routes use the existing local human administrator credential; a peer
certificate, agent token, guest token or non-admin JWT cannot administer them.
Request bodies reject unknown fields. Responses never return private keys or
stored invitation token hashes. The invitation token is returned exactly once
by create, with `Cache-Control: no-store`; validation errors omit raw input.

| Method and path | Meaning |
| --- | --- |
| `POST /api/v1/node/invites` | Approve intended node/certificate, local endpoint IP policy, local channel scopes, invitation and grant expiry. Return invitation bundle. |
| `GET /api/v1/node/invites` | Bounded invitation status list, without tokens/hashes. Expired pending invitations display expired. |
| `POST /api/v1/node/invites/{id}/accept` | Locally approve the issuer certificate/address and invitation, persist acceptance intent, redeem over pinned mTLS, confirm local mirror grants only after ACK. |
| `DELETE /api/v1/node/invites/{id}` | Revoke a pending invitation. Consumed invitations cannot be reopened. |
| `GET /api/v1/node/peers` | Bounded local-admin trust overview. |
| `DELETE /api/v1/node/peers/{node_id}` | Commit local relationship/grant/consent revocation and control outbox; close sockets. |
| `POST /api/v1/node/peers/{node_id}/certificate` | Explicit pin rotation with expected certificate epoch; invalidate old grants/consent and close sockets. Re-invite to authorize again. |
| `PUT /api/v1/node/peers/{node_id}/grants/{channel_id}` | Replace an active authority-owned scope with expected epoch; observer can only read. Revoked grants require a new invitation. |
| `DELETE /api/v1/node/peers/{node_id}/grants/{channel_id}` | Revoke only that local authority grant and enqueue its authenticated control notice. |

Separate peer listener:

| Method and path | Meaning |
| --- | --- |
| `POST /api/v1/federation/hello` | Exact version 1 and actual TLS identity. Pending invitation may authorize only bootstrap. |
| `POST /api/v1/federation/invites/{id}/redeem` | Prove invitation-bound TLS identity and secret, atomically consume and commit issuer pin/grants/receipt. |
| `POST /api/v1/federation/revoke` | Authenticated peer revocation notice; durable idempotent ACK. |
| `POST /api/v1/federation/grants/revoke` | Authenticated authority grant tombstone; no unrelated channel/peer revocation. |

Bootstrap requires a locally approved, unexpired invitation bound to the exact
node and certificate; it grants no channel traffic, participant listing, files,
admin APIs or local credentials. Revoked peers can authenticate only for the
revoke/ACK control paths (or a newly approved invitation). Key mismatch and
expired certificates still fail; revocation delivery never relaxes authentication.

`Scope.actors` holds node-qualified remote principals with the original wire
vocabulary `human`/`agent`; local authentication's `user` kind is not a wire alias. `Scope.role` is the local
admin-approved role of those remote principals for that shared channel, not a
local User/Agent impersonation. Scope intersects with grant state/epoch/expiry,
current Room existence/archive, current approving-admin authority and local
consent. `task.execute` additionally requires the injected local policy callback;
its default is deny. Files, shell, credentials and machine/admin actions are not
in the capability vocabulary.

## Persistence, replay and concurrency

Revision `064_peer_trust`, down revision **`063`** (the revision ID, not the
filename), adds peers, invites, acceptances, grants, consents, control events and
bounded-field audit records. #591's `065_shared_channels` depends on this revision.
The unmerged PR #583 has a separate migration branch and needs an explicit chain
resolution if integrated later. No operational database was migrated.

Only a SHA-256 hash of the random 256-bit invitation token is stored. Issuer
redemption and pin/grant activation are one transaction, serialized by an actual
SQL UPDATE on the peer row, including SQLite. Concurrent identical redemption
returns the one durable receipt; one consumption audit and one grant are written.
Changed identity/token, consumed or revoked scope and stale epochs fail closed.
Receipt recovery shares the current grant checks with normal authorization in
the same peer-locked transaction: active grant and matching epoch, current grant
expiry, authority-owned Room state, current approving-admin role and local
consent. Every channel in the receipt must pass. Neither issuer ACK recovery nor
confirmed acceptance replay can reuse a cached receipt after those approvals
are withdrawn. Receipt recovery itself does not start execution or invoke the
separate per-execution policy callback.

On the invited node, acceptance intent and issuer ACK confirmation are separate
local states. There is no distributed transaction. A lost ACK leaves local state
pending; re-submitting the same invitation retrieves the issuer's existing receipt.
The local node checks invitation identity, receipt shape/channel set, current pin,
epoch, expiration and remote revocation tombstones before confirming. Accepting a
remote authority grants only mirror delivery for its channels, never exposure of
channels owned by the accepting node. Reverse exposure needs another invitation.

Three namespaces remain distinct: peer certificate/trust epoch, authority grant
epoch, and local consent policy epoch. Neither a new pin nor control replay
revives revoked grants. Rotation closes existing sockets; even deliberately delayed
closure is safe because each HTTP request checks the current SQL pin again.

`deliver_controls()` performs one bounded outbox pass. It uses fresh pins and
locally approved destinations, leaves failures pending and marks delivered only
after the authenticated matching ACK. Integrators schedule subsequent passes;
there is no unbounded retry or silently started background task. Already shared
data cannot be clawed back, and an unreachable peer may not yet know a revocation.

## #591 and #592 transaction contracts

```python
async with session_factory.begin() as db:
    authorization = await peer_service.authorize(
        db, verified_tls_identity,
        sender_node_id=sender, authority_node_id=authority,
        channel_id=channel, principal=principal,
        action="message.send", grant_epoch=epoch,
    )
    # ONLY NOW inspect command dedup/receipt, apply effect and write event/outbox.
    # All use this db; none commits independently or performs external work.
```

The returned `AuthorizedPeer` is evidence for this transaction, not a reusable
capability. Peer locking serializes authorization/effect commit with revoke. A
separate-connection SQLite test pauses immediately after authorize while revoke
competes, proves effect commit precedes revoke commit, then denies any old request.
A revoked grant may not return an old cached success as authorization.

The mirror uses `authorize_delivery(db, verified_tls_identity,
authority_node_id=..., channel_id=..., grant_epoch=...)` before event dedup and
projection. It checks actual authority TLS identity, current pin, confirmed mirror
grant, expiry, administrator consent and remote tombstones without committing.
#591 also checks its remote-channel→local-Room mapping/archive in the same
transaction. Unrelated authority IDs and missing/uncertain authorization fail.

Neither function authorizes a fresh execution while the authority is unreachable.
#592 must obtain current authority confirmation, recheck grant/consent at delivery,
start and result acceptance, and apply local agent/workspace policy. It also drains
or cancels executions and retires runtime sessions when consuming revocation.
This PR records revocation durably and exposes the hooks; it cannot claim a process
is stopped before #592 supplies actual termination evidence.

Errors expose only codes: 400 invalid request/certificate/address; 401 missing or
mismatched transport identity/pin; 403 channel/principal/grant/scope/local policy;
409 stale/conflicting invitation/epoch/receipt; 426 unsupported version;
503 disabled peering/unavailable peer or authority. Outbound remote rejection is
502 without echoing the remote body.

## Local verification

From the repository root, with server/dev dependencies installed:

```sh
uv run --package anygarden --extra dev pytest packages/cluster/tests/test_federation_trust.py packages/cluster/tests/test_migrations.py
```

Tests use independent temporary SQLite files, generated disposable credentials,
real mTLS sockets restricted to loopback, and normal product services/routes.
They cover bootstrap, unknown/missing certs, spoofed headers, node/version mismatch,
concurrent single consumption, lost ACK/restart receipt replay, expired/revoked
scopes, role/action/principal boundaries, absent local execution approval, endpoint
SSRF and redirect rejection, pin rotation with an old socket kept alive, immediate
local revocation during delivery failure, authenticated control ACKs, mirror
admission, targeted grant tombstones, SQLite revoke/effect ordering, and migration
up/down/schema correspondence. Contract-derived human/agent examples cover the
full invitation and authorization path; issuer and accepting-node replay tests
withdraw administrator authority, channel state, consent, grant state/epoch and
grant expiry, including retry by a different currently authorized administrator.
No external peer, provider, runtime engine or
production database is contacted by these tests.
