# Phase 4 execution-reliability QA contract and completion criteria

Status: accepted architecture contract from task #29; executable implementation
and result are pending task #31's exact head. Baseline: `main` at
`422aa3543ed5586124caa0636659f43d908fd8eb`.

## Contract under test

The user-visible guarantee is deliberately asymmetric:

| Boundary | Required guarantee |
| --- | --- |
| Engine work | At least once. A recovery may invoke an external engine again. |
| User-visible response | At most once for one logical Turn/idempotency key. |
| Automatic recovery | One bounded retry by default, only for a retryable delivery/interruption condition. |
| Authority | Archive, removal/revocation, or explicit stop/cancel wins and cannot be undone by an old attempt. |

Every new logical Turn is durable and stores its stable request id, room,
target participant/agent, trigger message/thread, optional Task relationship,
immutable idempotency key, intent state, accepted completion, and retry budget.
Each Turn attempt has a monotonic attempt number, agent generation, opaque lease
fence token, lease/start/end/expiry fields, and outcome/reason. There can be
one active lease only. Dispatch is made from a transactional outbox; original
trigger-message persistence and Turn creation are atomic. Old `ActivityLog`
rows and old lifecycle frames remain observation-compatible during mixed
rollout. Legacy-capability turns are closed safely, not auto-retried.

## Baseline gap being closed

The baseline has an `AgentTurnTask(request_id, task_id, redispatch_count)`
correlation only for synthetic Task-assignment wakes. It does not identify a
logical user turn, attempt/generation, active lease, completion winner, or
idempotency receipt. `ActivityLog` is append-only; a lifecycle frame is
persisted without a unique attempt/event identity. The assignment redispatch
helper checks the old mapping then re-wakes in a later action, so it cannot
fence duplicate terminal frames or a liveness sweep against a late finish.
Unmapped user turns get a liveness notice rather than recoverable work.

The current Python and TypeScript clients reconnect with in-memory `since_seq`
and suppress duplicate message sequences only within that process. Their
in-process handler queues do not survive daemon/process restart and the replay
protocol does not prove a durable attempt/lease. The existing task redispatch
and orphan tests remain useful legacy coverage but are insufficient for
in-flight recovery.

## Required automated regressions

### 1. Schema, migration, and domain service

1. Upgrade a database that contains legacy `ActivityLog` and `AgentTurnTask`
   rows without losing their queryability. New durable Turn/Attempt/outbox rows
   are created only for new work and the mixed-rollout capability marker is
   explicit.
2. Persisting a triggering room message plus its Turn is atomic: injected
   failures leave neither a dangling intent nor a trigger that appears
   dispatchable without an intent.
3. The same immutable idempotency key returns the existing logical Turn and
   accepted completion. It neither creates another outbound delivery nor
   another user-visible room message.
4. Two contenders for a lease race through the service/database path. Exactly
   one conditional update succeeds; the loser observes the current attempt
   rather than creating a second active lease.
5. Attempt number increases monotonically after an eligible interruption;
   generation and opaque lease token change. A completed/cancelled/expired
   attempt cannot become active again.

### 2. REST/service and operator observability

1. A deterministic request creates one pending durable intent, then one
   dispatchable outbox item. Retrying the client request with the same key
   returns the same resource/completion according to the exposed API form.
2. Inspect the implementation's operator query surface and assert correlation
   of logical turn, attempt, generation, lease state, retry count, reason and
   terminal outcome. Counters cover pending, leased, retrying, completed,
   cancelled and expired paths.
3. A current-lease completion atomically stores one response and completes the
   Turn. A duplicate completion returns the accepted result without a second
   message; an expired, old-generation, or wrong-token completion causes no
   Task/message mutation and records `stale_completion` audit evidence.
4. A Task-backed Turn uses the same state machine. It may re-dispatch once
   only while the Task is unresolved, the assignee is still an active agent
   participant, and the room remains active. A plain user Turn is separately
   proved recoverable; it must not remain notice-only as in the baseline.
5. Explicit cancel/stop and a user-visible terminal engine failure do not
   retry. Retry exhaustion creates one explicit terminal failure notice and
   one terminal audit sequence.

### 3. WebSocket and machine lifecycle

1. A targeted delivery carries request id, attempt number and lease token.
   Accepted lifecycle and response frames must echo all three; absent or
   mismatched fencing values are rejected according to the implementation's
   protocol error and have no delivery side effect.
2. Reproduce #470 deterministically: start a user Turn, request an approved
   manifest/config generation change while its lease is active, and verify
   drain rather than immediate loss. On completion, exactly one response is
   retained; on confirmed process death/deadline, one bounded redelivery is
   issued under the next generation.
3. Drive concurrent or interleaved `handler_finished`, disconnect/daemon-loss
   sweep, and redispatch paths. One winner may complete or recover the Turn;
   no branch may make two active attempts, two re-wakes, or two visible
   responses.
4. Disconnect/reconnect and agent/daemon restart replay an outstanding intent
   to the current target through durable delivery. A replayed delivery may
   execute the engine again but all duplicate messages/late responses remain
   invisible to the room.
5. A completion from the drained generation or an expired lease is classified
   as stale. It cannot overwrite the winning completion, advance Task state,
   or trigger a fresh redispatch.

### 4. Authorization and cancellation fences

1. At dispatch, lease acquire, completion, and retry, archive/removal/revoked
   capability are re-evaluated. Each blocks the operation, cancels the Turn
   when required by contract, and leaves no wake or completion message.
2. A socket open before archive/removal is fresh-gated on the next lifecycle
   or completion frame. It closes with the established authorization protocol
   behaviour (currently 4003 where applicable) and writes no stale response.
3. Admin/user stop and explicit cancel fence an active attempt. A late frame
   becomes audit-only and never revives/reassigns the Turn or its Task.

### 5. Client and browser boundary

1. Python agent runtime tests simulate a reconnect and a process-local
   supervisor loss. They must use the server-delivered intent/attempt/lease
   payload rather than treating a reset in-memory `since_seq` cursor as
   recovery. Duplicate delivery is safely acknowledged/handled without a
   second visible completion.
2. TypeScript client tests cover the same restart/reconnect contract, including
   stale-lease lifecycle/response suppression. They stay deterministic and do
   not call an actual engine.
3. A browser flow is required only if Phase 4 exposes Turn/recovery state or
   an operator status UI. It will use a local deterministic API/WS fixture to
   show one completion after reconnect/reload. Otherwise browser remains out
   of scope and the durable protocol is covered through ASGI REST/WS tests.
   Real CLI/LLM execution is never part of this deterministic suite.

## Test layout and execution gate

The implementation head will determine final import/route names. Preferred
additions are a dedicated cluster recovery regression module plus focused
extensions to `test_ws_handler_redispatch.py`,
`test_liveness_orphan_notify.py`, lifecycle, Python-agent, and agent-ts client
suites. Test doubles must control clock/deadline, outbox delivery, generation
and connection loss; they must not mock the durable Turn/Attempt transaction or
authorization gate being asserted.

Before a final verdict, QA will run focused schema/service/WS and client suites
on task #31's exact commit, then relevant full cluster, Python-agent and
TypeScript suites. A browser smoke is added only for a new browser surface. The
exact head must also receive independent review and terminal hosted checks
before it is merge-ready.

## Handoff trigger

When task #31 publishes a runnable SHA/PR, QA will apply this matrix to that
exact head, first reproducing baseline duplicate/stale/orphan failure modes and
then asserting the durable protocol. Any mismatch in state names, public status
codes, or migration form will be aligned to the accepted implementation
contract rather than inferred from this planning document.
