# Durable shared channels (#591)

This implementation stacks on peer trust PR601 **18f8aeeff93a87c07152ae95025a3c9e579c1e63**.
Packaged schemas are exact copies of PR596 **aa402f5a3fdf58d17f7377fd5103d8e75f8380b1**.
Migration chain is `063 → 064_peer_trust → 065_shared_channels`; no deployed database is changed by this PR.

## Composition and public API

Construct `ChannelService(node_id=peer_service.node_id, peers=peer_service, sessions=peer_service.sessions)`
with the supervisor's persisted identity and SQL session factory. Pass it to
`create_app(config, channel_service=...)` for local JWT/API-token endpoints and to
`PeerListener(peer_service, ..., channel_service=...)` for the separate mTLS endpoint.
Both injections are optional. Ordinary startup enables neither peer listening nor sharing;
local shared-channel endpoints return `SHARING_DISABLED` until composed. Production
supervisor/configuration and the collaboration UI remain integration work for #594/#593.
No new identity file, certificate, endpoint, listener, credential or provider call is inferred.

All local routes live under `/api/v1/shared-channels`:

- `POST /bindings`: admin binds an **empty**, non-DM, active local Room to
  `{authority_node_id, channel_id, local_room_id}`. At the authority, channel ID equals
  Room ID. Mapping is immutable; there is no reset, history export, or automatic resync.
- `PUT /{channel_id}/publication`: current local admin explicitly approves/withdraws
  `{principal, active}` publication consent. Approval does not authorize execution.
- `POST /{channel_id}/participants`: authority admin commits
  `{operation_id, expected_revision, principal, role, active}`. Explicit publication
  consent and its approver are checked before dedup; a remote active principal also
  requires a current peer read grant. Removal requires no new publication consent.
- `POST /commands`: original v1 envelope only. Local authority messages require
  current Room write permission and publication consent. Remote-authority commands
  are durably queued as **unconfirmed**, with no Message projection or execution.
- `GET /{authority}/{channel}/submissions/{request_id}`: read the caller’s durable
  submission status after restart, without performing network I/O.
- `POST /{authority}/{channel}/submissions/{request_id}/retry`: explicitly deliver the
  same stored command over pinned mTLS; successful authoritative receipt changes the
  submission to **confirmed**. A timeout/lost response retains **unconfirmed** and the
  same ID. Receipt confirmation does not bypass ordered event replay.
- `POST /sync`: read scope `{protocol_version, sender_node_id, authority_node_id,
  channel_id, grant_epoch, actor}`. Fetch from the durable applied cursor, commit
  inbox/projection/cursor, then ACK. A lost ACK is safe to retry after restart.
- `GET /{authority}/{channel}?after_seq=0&limit=50`: current local membership plus
  current mirror grant/consent checks; returns confirmed text, thread root and origin,
  authority cursor, and roster including inactive tombstones.

The caller drives bounded retry/sync when reconnecting. No unbounded polling worker
or offline authority election is started. Previously received copies are retained;
local grant revocation hides this API projection but is not a remote deletion promise.
A participant event is a display projection, not a grant mutation. To remove access,
use #590's authoritative grant/principal revocation; roster delivery cannot substitute
for that policy change. Tests cover removed actors being refused before old receipt/replay.

Existing Room message/task/WS/search paths deliberately exclude shared Rooms. They
cannot bypass ordering, expose a revoked mirror, turn remote display principals into
local credentials, or deliver shared context to legacy room runtimes. Independent
local Rooms continue through existing APIs. Product UI integration uses the dedicated
snapshot/command API. Explicit trusted federation guards may call common room policy
with `allow_shared=True`, then apply their own current peer/execution constraints.

The peer listener alone exposes `POST /api/v1/federation/channels/{commands,replay,ack}`.
Replay and ACK use the read scope above plus `after_seq,limit` or `seq`. The TLS identity
is obtained from the server's verified ASGI scope; headers cannot supply it. No HTTP
peer route is mounted in the ordinary app. Transport I/O is outside SQL transactions.

## Transactions and extensions

`await commit_command(db, envelope, apply_effect=None, *, tls)` stages and returns the
**exact receipt schema**. Callers commit before ACK. Every call first validates the
closed command and current peer pin/grant/actor/action/consent, then locks the channel,
runs the current command guard, and only then consults the canonical-body receipt.
Changed-body reuse raises `ID_CONFLICT`. An effect exception rolls back the transaction.

`async apply_effect(db, envelope) -> CommandEffect(revision, state, process_state, task_status)`
may only change the supplied SQL transaction. It must not commit, launch processes or
perform network I/O. Effects, receipt, seq and outbox event commit together. The event
contains the original request and receipt unchanged, including task outcome/text/error_code.

#592 registers `effects[kind]`, `command_guards[kind]`, and DB-only
`projections[kind]`. **Every task kind requires a guard even for a cached receipt**;
unregistered task guards fail closed. The callback must enforce state transitions/CAS.
`submitters[kind]` may register a coordinator accepting `(envelope, *, tls)` and owning
the complete transaction lifecycle. The default `submit()` commits `commit_command()`;
#592's coordinator uses that same path and handles only designated late-result refusal
with rollback then a fresh authorized, bounded audit transaction. Projection completion
is not permission to execute; executor dispatch happens after commit and local fences.

Sequence allocation and grant checks use actual SQLite write locks, not ineffective
`SELECT FOR UPDATE`. Tests control two distinct database connections in both effect-first
and revoke-first orders. This proves the SQLite boundary, not PostgreSQL lock equivalence.

Inbox stores event ID + seq + canonical body. Gaps remain unapplied; the contiguous
cursor never skips them. Altered IDs/bodies/sequences are rejected. Participant control
shares the command sequence and requires exactly previous principal revision + 1.
Inactive rows, operation receipts, command receipts and event identity history are kept
for channel lifetime, including restart/re-add. A mirror starts from genesis; no snapshot
replacement can silently erase dedup history. ACK cannot exceed a contiguously delivered
range. Replay pages also respect the 64 KiB transport budget; an event above 60,000 UTF-8
bytes is rejected atomically instead of creating an undeliverable committed event.

Wire payloads are closed, text-only v1 contracts. Attachments, local paths, credentials,
engine/session handles and extra metadata are not exported as fields. Explicit links can
be included in selected text; neither node promises binary replication or link availability.

## Verification boundaries

`test_shared_channels.py` exercises two real SQLite files and product PeerService.
The transport test uses actual loopback TLS sockets and the pinned product HTTP client.
Other boundary tests inject verified certificate identities directly and do not claim
network coverage. Outage/lost-response tests use an explicit transport double. No external
LLM, remote provider, deployed node, or production database is contacted.

Covered: concurrent canonical dedup, altered ID/body conflict, both authority directions,
thread origin mapping, mixed message/participant replay, tombstones and revision CAS,
inbox/effect rollback, restart, lost ACK, missing seq, bounded ACK, role/grant revocation,
guard omission before cache, local admin demotion, human principal, denied spoofed headers,
unknown metadata/versions, offline unconfirmed state, local channel independence, and
migration column/primary-key/foreign-key parity plus downgrade/upgrade.
