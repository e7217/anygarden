# Phase 3 — message-linked Task and CAS-claim regression plan

## Purpose

This is the independent QA completion contract for Phase 3. It is written
against merged Phase 2 (`2a593520`) before the final Phase 3 API/model
contract is published in task #23. Tests will be made executable on the
implementation head and any provisional route or error code below will be
replaced by that contract rather than inferred from the current Task API.

## Current baseline and required additive behavior

The existing `Task` row has room/title/status/assignee fields, while the
existing create and update endpoints are ordinary create/read-modify-write
operations. It has no durable `message_id` relationship, no public CAS-claim
operation, and no caller idempotency key. Existing Task create/update/delete,
assignment-message fanout, and the agent self-status exception must retain
their behavior for unlinked legacy rows.

Phase 3 must add the link and claim semantics without turning a task row into
an ambient room-level agent wake. An assignment wake must identify the linked
Task and target only the assigned agent; it must be replay-safe and idempotent.

## Executable acceptance matrix

### A. Message ↔ Task relation

1. Creating a Task from an accessible message persists one non-null durable
   link and returns the same identifier in create, direct, list, and relevant
   WS task payloads.
2. A message has at most one linked Task. Repeat creation using the agreed
   idempotency key returns the original Task without another assignment
   message/wake; a distinct conflicting request receives the contract's
   conflict response and also creates no duplicate.
3. A cross-room message ID, inaccessible message, reply/root shape disallowed
   by contract, or deleted/invalid ID cannot mint or disclose a Task. The test
   verifies the Task/message counts do not change.
4. Legacy unlinked Task create/list/update behavior remains compatible, and
   migration rows retain NULL linkage as specified.

### B. Atomic claim and idempotency

1. Two independent authenticated actors race the same unclaimed linked Task
   using a start barrier. Exactly one conditional update succeeds; the loser
   receives the documented conflict/current-state result. Persisted assignee,
   status, timestamps, event log, and WS task event name identify one winner.
2. A retry by the winner with the same idempotency material is a no-op: no
   second state transition, assignment mention, agent wake, or replay record.
3. A retry by the losing actor remains a conflict and cannot overwrite the
   winner. Concurrent claim against already terminal/cancelled rows likewise
   cannot reopen it unless the final contract explicitly permits that
   transition.
4. The database-level predicate/constraint is tested independently of the
   HTTP handler so parallel request scheduling cannot accidentally hide a
   read-modify-write race.

### C. State and authority boundaries

1. The contract's transition table is covered for `todo`, claimed/in-progress,
   terminal, retry/requeue, and assignment/unassignment paths. Invalid
   transitions return the documented 4xx response with no mutation.
2. A member may perform only its own allowed claim/assignment operation; it
   cannot claim for another participant, reassign an owned Task, edit title,
   or alter unrelated status. An agent is restricted to the existing
   self-assigned status path plus any explicitly granted Phase 3 claim rule.
3. Owner/admin/global-admin capabilities obey the final matrix without
   bypassing the linked-message room check. Observer, outsider, and
   cross-room participant attempts return the contract 403/404 result and
   receive no task/assignment event.
4. Archived rooms reject all create/claim/write forms with the contract's
   archive status (currently write policy is 409); read/replay behavior is
   tested separately and must not imply write capability.

### D. Fresh WebSocket gate, reconnect, and agent wake

1. Every claim/update client frame gets a fresh authorization/state check.
   Removing the actor or archiving the room after connect then sending the
   frame closes/revokes with the Phase 1 fresh-gate code (currently 4003) and
   persists no claim.
2. A connected room subscriber observes the single winning task transition;
   a reconnect from its prior sequence cursor observes the linked Task and/or
   assignment message exactly once in global sequence order. Unauthorized
   subscribers cannot obtain either through live fanout or replay.
3. An agent assignment wake occurs only for a successful, targetable agent
   assignment/claim. Concurrent loser, no-op retry, human assignee, forged
   client metadata, archived/removed actor, and failed validation create no
   `message_received` turn or duplicate `AgentTurnTask` mapping.
4. The resulting assignment message retains the message-thread identifiers
   mandated by Phase 2 when the source message is a thread root/reply; agent
   follow-up uses that same root where the contract requires it.

## Test layers

| Layer | Evidence |
| --- | --- |
| Model/migration | 1:1 uniqueness, FK/room constraints, migration preservation, atomic conditional update |
| REST integration | Message-link create/list/direct, conflict/idempotency, transition and role matrix |
| WebSocket integration | Frame-fresh removal/archive, live winner event, reconnect/replay and non-disclosure |
| Agent integration | Assignment wake and dedup, Python/TS thread-root propagation where applicable |
| Browser E2E | Deterministic two-session claim race and visible single owner/state, with API-local fixtures to avoid LLM timing |

## Contract values awaiting task #23

- canonical migration number and relation direction (`tasks.message_id` versus
  a join/link table), including root/reply eligibility;
- create/link and claim endpoint/frame names, request idempotency field, and
  success/conflict/status codes;
- exact claim transition/state vocabulary and timestamp/audit payload;
- whether a successful claimant is automatically assigned, and whether agent
  self-claim is permitted;
- direct-id/list/search/saved exposure for linked Task references and the
  expected WS replay frame shape.

When these values are fixed, this plan becomes the focused regression module
and the same test identifiers are run again on the final implementation head
followed by the full cluster and deterministic browser suites.
