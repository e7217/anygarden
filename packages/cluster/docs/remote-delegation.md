# Remote task delegation (#592)

This module connects authority-side Task decisions to the standalone local
execution manager. It does not register HTTP routes, run a federation daemon,
create grants, choose workspaces, or weaken existing generation/lease fencing.
Those node and channel integrations own the callbacks described below.

## Authority transaction

Construct `DelegationService(authority_node_id, resolve_principal, *,
executor_allowed)`. The resolver maps the *whole* `(node_id, kind, principal_id)`
within the channel to an explicit local Participant mirror. It must never use an
unqualified remote ID as a local primary key. A missing mapping, observer role,
or archived room denies work. `executor_allowed(db, channel_id, executor)` is a
required live predicate checking the current grant epoch, actors and
`task.execute` capability for the executor named by `task.request` (and for the
actor of every other task command except `task.cancel`, which the requester
owns).

Register `delegation.install_guards(channel_service)` before dispatch. It
registers both the guard and the effect for every task kind. #591 rejects
**all task commands** if their guard is absent, including receipt replay.
After current peer/grant/export/principal checks and before receipt dedup, the
registered `authorize_command` checks current local membership and room state.
The request also checks the executor's current membership. The authority callback
is `await channel_service.commit_command(db, envelope, delegation.apply_effect,
tls=tls)`; its caller commits before returning a receipt. Guard, dedup, Task CAS,
delegation CAS, channel receipt and channel event belong to that one transaction.
On SQLite the caller reserves its writer before any reads. PostgreSQL role/room
rows are locked and Task/delegation updates additionally use compare-and-swap.

The callback returns the four schema fields `revision`, `state`, `process_state`,
and `task_status`. ChannelService constructs the exact PR596 wire receipt/event.
The callback performs no process or network operations and never commits.

| Delegation state | Existing Task status |
| --- | --- |
| requested / rejected | todo |
| accepted / running | in_progress |
| completed | done |
| failed / cancelled | failed |
| cancel_requested / unknown | blocked |

One active reservation owns the Task. Rejection releases both reservation and
assignee; an accepted execution ID is unique within its executor node. Every
transition matches authority, channel, delegation revision, executor identity,
execution ID, current Task status and current assignee. Unknown cannot accept
another result or restart; cancellation remains available. Existing local Tasks
are untouched. Runtime failure codes are closed; result text uses the original
`payload.outcome/text/error_code` fields.

`task.request.payload.source_message_id` is the **wire** message ID. It resolves
through the `SharedMessage` mapping of the authority's channel stream; the wire
ID is never compared against the local `Task.source_message_id`, which stores the
projected local message ID. A missing mapping or a thread reply is denied before
the reservation is taken.

## HTTP coordination and follower projection

`await delegation.submit(channel_service, envelope, tls=tls)` owns the outer
commit/rollback boundary for task commands. The success path is one transaction:
authorize, guard, dedup, effect, receipt/event, commit. Only `LateResultAfterCancel`
leaves it; the coordinator then rolls back, re-runs current peer/grant/principal
and delegation authorization in a **separate** audit transaction and re-raises the
original conflict. Register it with `delegation.install_submitters(channel_service)`
so #591's `submitters["task.*"]` registry — the path its `/commands` router enters
through `ChannelService.submit` — routes every task kind here. The audit's peer
reauthorization calls the public `PeerService.authorize` directly; no private
ChannelService entry point is used. Oversized result bodies degrade
to `task.unknown` before any wire write, keeping the envelope under the event
size limit.

Guards without submitters would let normal commands run on the default
single-transaction path while losing the late-result audit, so the guard itself
fails closed with `SUBMITTER_REQUIRED` — for new and replayed commands alike —
until the coordinator is registered. The four registration states are therefore
all safe: nothing registered → `COMMAND_GUARD_REQUIRED`; submitter only →
`COMMAND_GUARD_REQUIRED`; guard only → `SUBMITTER_REQUIRED`; both → normal.

On a follower (non-authority) node, `install_projections(channel_service)`
registers a DB-only `DelegationMirror` projection for every task kind. The
projection never creates or mutates a local Task and never schedules execution;
it validates the already-committed event against the same state/revision/
execution rules and records the authority-confirmed mirror row. Local execution
starts only from the executor bridge after its own accepted-launch checks, not
from this projection.

## Rejected late result audit

Only `LateResultAfterCancel` enters this path. Roll back the main transaction,
then begin a **new** transaction with the same current grant serialization
boundary and call `record_late_result(db, envelope, reauthorize=...)`. The required
reauthorize callback repeats current peer/grant/principal checks before audit
dedup. The service checks current local membership, room, same execution and
cancel state again. Commit only the bounded local observation, then return the
original conflict. No result body, channel seq, receipt/event, or Task mutation
is allowed. Other failures must not be routed here.

## Executor ownership and callbacks

`ExecutorBridge(sessions, directory, runtime, node_id=..., permits=...,
reauthorize=...)` owns one LocalExecutionManager. The node must provide one bridge
for its configured durable runtime directory; the runtime lifetime file lock
rejects a second owner of that directory. Keep the DB and runtime directory
stable together across restart. Do not point multiple managers at different
runtime directories for the same node's bindings.

`permits(ExecutionFence)` is a required **synchronous current-policy predicate**.
It checks the local agent generation, current lease token/expiry, workspace and
policy epochs, grant epoch, and current role. The bridge installs this exact
predicate into the manager, which rechecks it on queued launch and at the native
stdin write boundary. It must read live local policy, not a captured admission
boolean. When policy changes, deny it first and call `await bridge.revoke(scope)`;
this trusted cancellation works after receipt access has been denied. A newer
lease/generation requires a new scope policy epoch. Never re-enable a retired
scope. Any existing local lifecycle/turn owner remains responsible for its
normal stop/restart/lease rules.

`reauthorize(db, binding)` is the required durable local grant/identity guard. It
must serialize with policy revocation using the same DB transaction boundary.
It runs before existing outbox receipts are returned and before every delivery.
Transport callbacks additionally perform current authenticated peer checks; this
module is not an mTLS implementation.

1. Materialize an Invocation under local policy, with a stable execution UUID.
   `prepare(..., generation, lease_token, grant_epoch)` stores its digest, scope,
   fence and accept command, **without** prompt, environment, paths or credentials.
2. `deliver(outbox_id, send)` sends only a committed outbox body. `send` validates
   wire schemas and peer identity, returning only after authority commit. ACK
   loss leaves the same immutable request ID/body pending; replay reauthorizes.
3. `launch(delegation_id, invocation, confirm)` requires the acceptance ACK and a
   freshly authenticated AuthoritySnapshot. It commits `launch_intent` before
   calling the manager. It never launches again from this state.
4. `observe` reads the durable local receipt and commits a started/result/unknown
   command. It does not send. Pending earlier commands must be ACKed before the
   next revision can be emitted. Empty/oversized final output becomes unknown,
   rather than truncated success; wire text is limited to 16,384 characters and
   a conservative UTF-8 byte budget within the 64 KiB envelope limit.
5. `cancel` consumes a fresh authenticated cancellation snapshot, supersedes
   unsent earlier results and cancels the local execution. Cancellation reports
   `not_started` only before launch intent; after intent it requires runtime
   proof of stop/finish. Missing or ambiguous runtime evidence yields unknown.

Recovery must call observe/reconcile for existing bindings. A launch intent with
no runtime receipt becomes unknown, even if the crash may have preceded spawn.
An existing terminal receipt can be delivered after restart without a second
start. Neither a lost ACK nor an unavailable authority authorizes a fresh run.

## Validation boundaries

`test_federation_delegation.py` uses file SQLite, independent DB connections,
PR596 schemas and the actual LocalExecutionManager with a controlled Runtime.
Its peer/grant/channel adapters are explicitly **test doubles**, not proof of
#590 mTLS or #591 replay integration.

`test_delegation_channel_integration.py` runs against the **actual** #590
PeerService and #591 ChannelService from `d46d272`: real grants,
wire-schema validation, channel sequence/receipt/event log, replay, `SharedMessage`
source mapping, the `/commands` router entry (`ChannelService.submit` →
`submitters` registry → the delegation coordinator), guard-absent and
submitter-absent failure for new *and* replayed commands, role/peer revocation
before replay, the late-result audit boundary through the public
`PeerService.authorize` reauthorization, and follower `DelegationMirror`
projection through the real `receive` path. The injected TLS identity is the
test double; no real network or external node is involved. The
full migration chain `063 → 064_peer_trust → 065_shared_channels →
066_remote_delegation` (upgrade, downgrade, head pins) is covered by
`test_migrations.py` and `test_federation_trust.py`.

Remaining explicit gaps: app-level mounting and two-node acceptance are #594
scope. No test in these modules invokes an external provider or an external node.
