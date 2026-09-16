---
id: 7
title: Independent nodes and single-authority shared channels
status: proposed-for-implementation
date: 2026-09-16
---

# 7. Independent nodes and single-authority shared channels

Implements the design deliverable of #587 under #586. Baseline freshly verified:
`66235f332f6538bceb1591f1c8b12bde4dbbb7ef`. This document and the executable
contract examples are **not an implemented federation service, transport security
proof, runtime compatibility certification, or two-node acceptance result**.

## Current code and target

```mermaid
flowchart LR
  S[Server: rooms/tasks/turn DB] -->|machine WS: desired state| M[Machine daemon]
  M -->|spawn| A[Python or TS Agent]
  S <-->|room WS: work and results| A
  A --> R[Codex CLI / Claude SDK / other engine]
```

```mermaid
flowchart LR
  subgraph A[Node A: one install and start command]
    CA[Collaboration + authoritative local DB]
    EA[Execution backend + local policy]
    RA[Runtime subprocess or SDK worker]
    CA --> EA --> RA
  end
  subgraph B[Node B: independent identity / DB / workspace]
    CB[Collaboration + local DB]
    EB[Execution backend + local policy]
    RB[Runtime subprocess or SDK worker]
    CB --> EB --> RB
  end
  CA <-->|authenticated scoped channel protocol| CB
```

| Existing source | Preserve | Adapt in later issues |
|---|---|---|
| `packages/cluster/anygarden/cli.py` | Existing server/machine/agent commands | #588 adds unified start/stop |
| `scheduler/lifecycle.py`, `scheduler/machine_bus.py` | generation, desired state, dispatch lease, ACK checks | #588 local backend; no direct DB bypass |
| `packages/machine/anygarden_machine/daemon.py`, `spawner.py` | process tree supervision, local materialization | #588 remove network requirement from local path |
| `task_service.py` | one-DB atomic claim and status CAS | #592 routes shared mutations to channel authority |
| `turns/service.py` | durable outbox, generation/attempt/lease fencing, completion CAS | #589/#592 execution adapter and receipt linkage |
| `workspaces/` | local consent, attachment epoch, revocation, write restrictions | no remote policy override |
| `packages/agent/anygarden_agent/integrations/codex_cli.py` | CLI invocation and JSONL parser | #589 remove ChatClient dependency and unsafe resume fallback |

PR583 changes lifecycle/daemon report concurrency, PR584 adds E2E, PR585 changes
release gating. They remain outside this baseline and need explicit integration
review when their overlapping code is adopted; this ADR does not authorize merge.

## Local node and execution interface (#588 / #589)

`anygarden start` runs the unified node in the foreground; `anygarden stop`
requests graceful shutdown of the owner of the same data directory. Existing
`server`, `machine`, `agent`, `client` commands keep their current behavior.
Existing data requires an explicit backup/migration procedure; startup must not
silently initialize a second DB or run destructive migrations.

`node_id` is an immutable UUID persisted with the node data directory, unrelated
to hostname, URL or Machine PK. A local Machine row remains an internal mapping
for existing foreign keys. DB/identity backup is restored together; a cloned
installation must get a new node identity before connecting. URL/key rotation
does not change identity; accepted key rotation requires local admin confirmation
in v1. No automatic authority failover or identity duplication is supported.

Integrated mode supports one API worker. Multiple workers are rejected; multi-worker IPC is deferred. Reload is rejected unless ownership release and child cleanup are tested. Existing server command behavior is preserved.

A process-lifetime OS lock protects the data directory. A second start fails;
PID files alone are insufficient. An independently started daemon cannot own
the integrated local Machine. A stop endpoint is local-owner authenticated,
never an unauthenticated network endpoint. Stop drains/cancels work, persists
receipts, reaps owned process trees and releases the lock last. Startup checks
remaining children/receipts before allowing a new execution. Unverifiable old
execution becomes `unknown`; it is never blindly spawned again.

The existing MachineBus-shaped boundary may be retained:

```python
class ExecutionBackend(Protocol):
    async def send(self, machine_id: str, frame: dict) -> bool: ...
    def is_connected(self, machine_id: str) -> bool: ...
    def connected_ids(self) -> set[str]: ...
```

`True` means delivery accepted, not process started or work completed. Local
ACK/report callbacks pass through the same lifecycle conditional updates as
remote reports (placement, generation, desired state, dispatch lease/token).
Local and network backends must never both dispatch for the same Machine.

The runtime boundary for #589 is `capabilities()`, `start(invocation)`,
`events(execution_id)`, `cancel(execution_id)`, `reconcile(execution_id)`.
Invocation IDs and receipts are durable local execution-manager data; adapters
do not claim tasks or decide retries. Cancellation returns a request receipt;
only confirmed process-tree termination yields `stopped`. Runtime capability
flags include progress, cancel, resume, and workspace enforcement. Unsupported
capabilities fail explicitly, never degrade silently to a broader permission.

## Identity and authority

All IDs in the wire examples are UUIDs. Agent identity is the tuple
`(node_id, agent_id)`; human identity uses `(node_id, user_id)` through the same
principal shape. Display names are never authentication identities. Channel
identity is `(authority_node_id, channel_id)`. Remote IDs map to local mirror
rows explicitly, never by reusing a foreign node's primary key.

Each shared channel has exactly one immutable authority in v1. It owns message
sequence, membership grants, thread roots, task originals, claim/complete/cancel
ordering and final task projection. A mirror cannot run the local task CAS to
confirm a shared mutation. Execution nodes own local consent, agent exports,
workspace/policy/credential access, process state and execution receipts.
Neither authority implies the other. Local channels have no federation export.

Shared Task status keeps the existing `todo/in_progress/blocked/done/failed`
vocabulary. The separate delegation record has `requested/accepted/running/
cancel_requested/completed/failed/rejected/cancelled/unknown`. The UI also distinguishes
an **unconfirmed submission** from an authority-committed `requested` record.
`requested` refers to an existing authority-owned source-linked Task and reserves one target agent at the authority; `accepted` commits its
claim (`in_progress`), but cannot start until execution node durably observes
that authority decision. `cancelled` is a delegation terminal; its Task projects
to `failed` with a cancellation reason, not a newly invented Task status.

## Peer admission, scope and revocation (#590)

V1 uses explicit HTTPS addresses and mutual TLS with peer certificates pinned
to immutable node identity after local admin approval. No discovery/relay/NAT
traversal. Local provider-free tests use isolated loopback transports only;
production cannot silently fall back to plaintext or disable peer verification.
Never forward credentials on redirects. User-supplied peer URLs require an
explicit admin-approved endpoint policy to prevent SSRF (including redirects,
DNS changes and metadata endpoints); private addresses are allowed only by that
local policy, not through arbitrary remote input.

Invite lifecycle: `pending -> accepted | rejected | expired | revoked`.
An invite is single-use, expiring, bound to issuer, intended node/certificate
fingerprint and proposed channels/scopes. Redemption and key pinning are atomic;
replay returns an already-consumed result, never another grant. Both nodes
approve their own exposure; accepting a peer grants no ambient room listing,
host shell, machine administration or runtime credentials.

A grant binds peer + channel + exported principal set + capabilities
(`channel.read`, `message.send`, `task.request`, `task.execute`, `task.cancel`)
with expiry and monotonically increasing `grant_epoch`. This envelope references
the **authority-issued channel grant epoch**, shared on that channel; execution
nodes additionally check their own local consent/policy epoch without putting
it into the authority namespace. Scope is intersected with current room role,
archive state, export policy and local workspace approval. Revalidate on every
request, replay, delivery, execution start and result acceptance. Authenticate
transport identity and compare it to the claimed sender before dedup lookup.

Revocation advances the epoch and stops new reads/writes/dispatch immediately
at the revoking node. A durable control event informs the peer; local removal
kills/drains affected executions and invalidates session bindings. Already
shared data cannot be clawed back. During a partition the other node cannot
know instantly: no new shared start is permitted without current authority
confirmation; an already running process may continue until its local deadline,
revocation delivery, or local stop. Cancellation stays unconfirmed until proof.
Results arriving under revoked grants are rejected; only bounded local audit
receipts remain. Re-invitation requires a new grant, never revives old requests.

### Bootstrap, certificate rotation and control-only authorization

Before registration, an active, unexpired invite may authorize only `hello` and
`redeem` from the exact node/certificate identity bound by the inviting admin.
This is a separate bootstrap permission, not general peer trust. The TLS layer
must prove possession of that certificate's key and bind it to the actual
connection; a node ID, certificate blob or proxy header supplied by the caller
is not evidence. Certificate-chain/hostname rules and explicit pin policy are
applied by the transport, never replaced by accepting all certificates. Pending
invite identities cannot list channels, enumerate participants, invoke management
APIs or send normal federation commands. An expired/revoked/consumed invite grants
no new authority; an authenticated same-request receipt replay is recovery only.

Invite consumption uses a conditional single-use transition and stores the local
acceptance, pin/grants and redemption receipt in one **local DB transaction**.
Concurrent different consumers cannot both succeed. Store local acceptance and
remote acknowledgement separately: no distributed transaction is implied across
nodes. After ACK loss, the same authenticated node retries the same request ID
and identical invite/body to recover the committed receipt; a changed body,
identity or key is rejected. Recovery rechecks current revocation/expiry policy
and cannot re-enable a grant. The UI/API must not infer the other node's acceptance
from local commit, and dispatch requires the relevant current authorities.

Explicit admin certificate rotation commits the new pin and a monotonically
increased **certificate generation** atomically, then closes old connections.
Each subsequent request, including control messages and receipt replay, must
revalidate its verified connection certificate against the current pin/generation.
An old socket is denied even if asynchronous closure has not finished. Old-key
bootstrap invites are invalidated rather than providing a backdoor to the retired
pin. Certificate generation, authority-issued channel grant epoch, and execution
node local policy epoch are independent counters. Rotation neither restores a
revoked relationship nor renews channel grants. No implicit old/new pin overlap
is permitted in v1; a future grace-window policy would need an explicit contract.

Relationship revocation atomically disables local channel access and records a
control outbox event. Failed delivery cannot roll back that local decision.
A **control-only permission** may still authorize authenticated `revoke` and its
ACK, scoped to this peer relationship, its revocation epoch and event/receipt ID.
This is not an authentication exception: the peer must still prove the currently
pinned TLS identity; missing, forged or retired certificates remain rejected.
It grants no channel reads/writes, task actions, participant discovery, grant
creation, certificate rotation or general management API access. Control replay
is idempotent; obsolete epochs cannot reverse newer revocation. If current peer
identity cannot be authenticated, retain bounded pending delivery/failure status
locally instead of weakening authentication to deliver the notice.

#590 acceptance must include real loopback TLS identity extraction, pending-invite
access denial, simultaneous consumption with one winner, ACK-loss receipt
recovery, pin rotation while an old connection stays open, revoked-grant survival
across rotation, immediate local revoke with failed outbox delivery, and negative
tests proving control-only credentials cannot access ordinary channel routes.
These checks are implementation requirements; the Phase 0 command model does not
claim to execute TLS or invite/rotation transactions.

## Versioned wire contract and APIs (#590–592)

Normative envelope and payload shapes: `contracts/federation/v1/envelope.schema.json`.
Unknown fields/kinds and protocol versions are rejected. First version is exactly
`protocol_version=1`; no implicit downgrade. `hello`/local peer configuration
must establish version 1 before channel traffic; capabilities cannot grant access.

Proposed endpoint contract (not currently mounted routes):

| API | Caller / owner | Meaning |
|---|---|---|
| `POST /api/v1/node/invites` | local admin | create bounded pinned invite |
| `POST /api/v1/node/invites/{id}/accept` | local admin at invited node | approve local scope and redeem on issuer |
| `DELETE /api/v1/node/peers/{node_id}` | local admin | revoke local relationship/exports and propagate control |
| `POST /api/v1/federation/channels/{channel_id}/commands` | mutually authenticated member to authority | envelope; returns durable receipt or closed error |
| `GET /api/v1/federation/channels/{channel_id}/events?after_seq=N` | authorized member to authority | ordered durable events, bounded page (max 100) |
| `POST /api/v1/federation/channels/{channel_id}/acks` | authorized member to authority | highest contiguous committed event cursor |

Membership/peer lifecycle are control-plane operations, not generic `message.send`
payloads. #590 must supply their route models/tests before implementation is GO.
This version's executable examples cover channel envelopes, not certificate
cryptography or invite HTTP handlers. Responses use HTTP 400 invalid schema,
401 unauthenticated, 403 scoped authorization denial, 409 state/ID conflict,
426 incompatible protocol, 503 authority unavailable. Errors never include
remote paths, credentials, raw stderr or full submitted bodies.

`request_id` is sender-generated and stable over transport retries. Dedup key is
`(sender_node_id, authority_node_id, channel_id, request_id)`. Same semantic body
and envelope returns the stored receipt; same key with changed content is
`ID_CONFLICT`. The receiver computes canonical content (sorted JSON object keys,
compact UTF-8, no floats/NaN, no duplicate object keys) and compares stored bytes,
not a client-supplied digest. JSON property order changes do not create new work.

Authority allocates `event_id` and contiguous channel `seq` in the transaction
that commits command receipt + state CAS + outbox event. Crash before commit
means no ACK; lost ACK means resend same request, not new execution. Followers
ACK only after their inbox dedup + projection + cursor commit. Delivery is at
least once. An event ID with changed bytes or reused sequence with another event
is an integrity error. A gap pauses projection, requests missing events, and
never advances the ACK cursor past the gap. Keep receipts/tombstones for channel
lifetime in v1; resetting cursor or dropping dedup history requires an explicit
resync protocol, not automatic replay into a fresh DB.

Message sequence and thread root are authority-local; a thread references an
existing root in the same channel. Cross-channel roots fail. Message envelopes
contain only selected text and explicit shared IDs; no automatic history,
credentials, workspace paths, runtime session handles or attachment replication.

## Delegation transitions and races

Every mutation includes expected `revision`; authority updates delegation state,
revision, task ownership and outbox atomically. Request payload's executor is a
node-scoped exported agent. Runtime input comes from explicit task/message
references, not arbitrary shell commands or caller-supplied local paths.

| Command and actor | Before → after | Required guard |
|---|---|---|
| task.request / permitted requester | absent → requested | expected revision 0; target exported; task reservable |
| task.accept / selected executor | requested → accepted | fresh local consent and authority reservation; claim CAS |
| task.reject / selected executor | requested → rejected | no process started; release reservation and assignee; Task stays todo |
| task.started / selected executor | accepted → running | matching execution ID; durable start receipt |
| task.result succeeded / selected executor | accepted or running → completed | same execution, revision, attempt; valid grant; completion CAS |
| task.result failed / selected executor | accepted or running → failed | known terminal failure; execution termination confirmed; closed error code |
| task.cancel / original requester or channel admin | requested/accepted/running/unknown → cancel_requested | revoke new-start permission before sending cancel |
| task.cancelled / selected executor | cancel_requested → cancelled | confirmed `not_started` or `stopped` process state |
| task.unknown / selected executor | accepted/running/cancel_requested → unknown | cannot prove execution outcome; no automatic retry |

The executor proposes a unique `execution_id` in accept; the authority binds it
per delegation. Local durable mapping from delegation to this ID is written
before launch. Claim accepted but ACK missing: reconcile the same receipt. Crash
between start intent and child observation: `unknown`, not another process.
Multiple claims compete in the authority transaction; one wins, losers get 409.
Distinct request IDs cannot bypass delegation identity/revision/terminal guards.

`task.result` is a tagged union: `outcome=succeeded` carries selected result
`text`; `outcome=failed` carries a closed `error_code` (`ENGINE_ERROR`,
`TIMEOUT_STOPPED`, `UNSUPPORTED_RUNTIME`, `POLICY_DENIED`), without raw stderr or
exception text. A failed outcome requires definite terminal execution evidence.
A timeout with an unconfirmed process stop is `task.unknown`, not known failure.
`process_state=finished` means a terminated invocation, not success; outcome and
Task status express success or failure. Pre-start policy refusal uses task.reject;
a failure discovered after an accepted invocation uses task.result failed only
when no execution remains unresolved.

Task projections (in the same authority transaction as the delegation):

| Delegation | Existing Task status | Reservation / ownership |
|---|---|---|
| requested | todo | one reserved exported target |
| accepted / running | in_progress | confirmed target owns claim |
| completed | done | retain terminal owner and receipt |
| failed | failed | retain terminal owner and closed reason |
| cancel_requested / unknown | blocked | retain reservation/owner; no reassignment or automatic retry |
| cancelled | failed | retain owner and cancellation reason |
| rejected | todo | clear reservation/assignee; retain rejected delegation tombstone |

After rejection only an explicit new request with a new delegation ID can reserve
the task again. Replayed rejection returns the old receipt; it must not clear a
new delegation's reservation. Old acceptance/result cannot reactivate a rejected
delegation. A duplicate failed result returns its receipt; reusing its request ID
as success is ID_CONFLICT. A distinct successful-result ID cannot overwrite a
terminal failed delegation.

If success or known failure commits first, cancellation fails as terminal. If cancellation commits
first, either result outcome fails `CANCEL_PENDING`; process confirmation must still arrive. Authenticated late results produce a bounded local observation receipt (execution ID + reason only), but never promote the task to completed or republish the result. This preserves evidence that side effects may already have happened. Process state is tracked independently from delegation state.
`cancel_requested` never means stopped. `unknown` never means success or permission
to replay. A human may reconcile an unknown result or authorize a **new**, audited
delegation after checking side effects; v1 has no automatic retry transition.
Transport duplicates of a successful result get the old receipt without another
visible message. Late attempts, stale grant epochs/revisions and changed body
reuse fail closed. External tool effects cannot be made exactly-once by this
protocol; only authority state/result publication is at most once.

## Sequence examples

```mermaid
sequenceDiagram
  participant CLI as anygarden start
  participant N as Node supervisor
  participant L as Local backend
  participant R as Runtime worker
  CLI->>N: acquire data-dir lock, load identity/DB
  N->>L: reconcile old receipts/processes
  L-->>N: ready or unknown (no fresh launch)
  N->>L: generation + lease-bound launch
  L->>R: create isolated worker
  L-->>N: lifecycle report through existing CAS
```

```mermaid
sequenceDiagram
  participant U as Requester
  participant A as Channel authority
  participant B as Execution node
  participant R as Runtime
  U->>A: task.request (stable request ID)
  A-->>B: committed request event
  B->>A: task.accept (local consent + execution ID)
  A-->>B: committed claim receipt
  B->>B: persist start intent, recheck local policy
  B->>R: invoke once
  R-->>B: progress / result
  B->>A: task.result (revision + execution ID)
  A-->>U: committed completion event
  Note over A,B: Lost ACK: resend same ID. Authority offline: retain unconfirmed outbox; no fresh shared start.
```

## Session scope, migration and first runtime

Select `codex-cli` first because the repository already invokes a separate CLI,
parses structured events and stores native resume handles. `Agent.runtime`
(`python|typescript`) is the legacy wrapper implementation language, **not** the
engine identifier. Do not overload that DB field with `codex-cli`.

Session key is `(execution_node_id, agent_id, authority_node_id, channel_id,
thread_root_id-or-main, workspace_binding_id, workspace_epoch, policy_epoch,
engine, engine_version)`. Never send native handles to peers or reuse a session
across channels/threads/workspace epochs. Explicit task IDs bind individual turns
within that scope; concurrency is serialized per session. Membership/export or
policy changes retire the binding to prevent prior private context leaking.
Legacy room-only session files are quarantined on migration: do not import them
into shared scopes. Start fresh only for a new, explicitly accepted invocation,
not by replaying a possibly completed old turn.

Current `codex_cli._call_codex` retries any nonzero resumed invocation as a new
session; #589 must remove that broad retry in the new path. Only a proven
pre-execution session-not-found outcome can permit a separately recorded fresh
attempt; otherwise surface `unknown` and require reconciliation.

Historical smoke image pins CLI 0.146.0; this is fixture provenance only. PM
reports host CLI 0.154.0; **no actual supported-version claim is made here**.
#589 must pin the first supported CLI version after provider-free argument,
JSONL, cancellation and session-restart checks. Live provider success remains
separate (#578). Until then capabilities are design targets: progress from
allowlisted JSONL events; cancellation from supervised process-tree termination;
resume only within the exact scope; external workspace writes remain disabled
unless existing local enforcement requirements are proved. Parsing a recorded
fixture does not certify a current CLI or model. Tokens/credentials stay local.

## Deliverables, implementation ownership and limits

- This change: ADR, envelope schema, normal/adversarial fixtures and a pure
  deterministic reference checker in `contracts/federation/v1/`.
- #588: developer owns CLI/lifespan/local backend and lifecycle integration.
- #589: adapter developer owns invocation/receipts/session migration.
- #590: peer auth, invite lifecycle and durable grant revocation.
- #591: transactional receipt/event/outbox/inbox and replay APIs.
- #592: authoritative task projection plus executor coordination.
- #593: UI must show unconfirmed submission, authority/executor offline,
  cancellation pending and unknown outcome separately.
- #594: independent durable two-node harness; its tests must distinguish
  reference-model validation from exercising actual product implementations.

The contract checker is in-memory and single-threaded. It tests decisions and
fixtures only, not SQL atomicity, fsync, TLS, race linearizability, process kill,
network partitions or external provider behavior. These remain explicit
implementation acceptance gates. No deployed schema or service is changed.
