# Phase 2 message-thread QA contract and completion criteria

Status: draft pending task #18's canonical contract.  This records the
current-code gap and the regression shape task #19 will implement against the
Phase 2 head.

## Current gap

`messages` has only room-scoped `seq`; it has no parent/root relation.
`GET /api/v1/rooms/{room_id}/messages`, `MessageOut`, the WebSocket `send`
frame, WebSocket replay, and FTS search likewise expose no thread identity.
The Phase 2 migration and every wire surface must therefore be validated as a
single change; adding only a DB column is not sufficient.

## QA contract proposal

Until task #18 changes it, the regression suite will expect these invariants:

| Concern | Completion criterion |
| --- | --- |
| Identity | `thread_id` is the root message id on both a root and every reply. `parent_message_id` is null for a root and is the root id for a reply. |
| Shape | A reply may target only an existing root in the same room. A reply may not target itself or another reply; two-level nesting is rejected. |
| Ordering | `seq` stays room-scoped and monotonically assigned across roots and replies. REST history and WS replay remain ascending by `seq`; no separate per-thread sequence is introduced. |
| Read surfaces | Root and reply fields are present on REST history, live WS `message`, WS replay, and search results. Pagination may split a thread, so every returned item carries its canonical `thread_id`. |
| Authorization | Creation uses the same room capability as a normal message; observer/nonmember are denied, archive writes are rejected, and direct parent identifiers never disclose another private room. |
| Freshness | An existing WebSocket is checked on each thread-reply frame. Archive, removal, or role downgrade closes it with the established 4003 contract and persists no reply. |

The exact REST routes and validation status codes will be aligned to task #18.
The current authorization convention is preserved: capability/membership denial
is 403, archived writes are 409, and WebSocket authorization revocation is
4003.  For a visible same-room but invalid parent shape, the default proposal
is a validation failure; the final code and tests will use the contract's
chosen 4xx form rather than inventing a parallel error taxonomy.

## Required automated regressions

### Model, migration, and service

1. Migration upgrades existing messages as roots without corrupting
   room/participant/seq data, creates an indexed parent relation, and has a
   compatible downgrade path.
2. A root plus multiple replies persists a common root `thread_id`, direct
   `parent_message_id`, and room-wide consecutive sequences under concurrent
   append.
3. Same-room root validation succeeds. Missing parent, parent in another room,
   self-parent, and reply-to-reply each fail without inserting a message.
4. A database/service attempt cannot construct a cross-room relation even if a
   caller bypasses the normal request schema.

### REST and search

1. Create a root then replies; history returns canonical parent/root fields and
   a thread query (if supplied by Phase 2) returns the root plus its replies
   in the contract's order.
2. `since_seq` replay and first/latest-page pagination neither duplicate nor
   lose replies at page boundaries; every returned result identifies its root.
3. Search finds reply content and returns `message_id`, `parent_message_id`,
   and `thread_id`; a result can be navigated back to its parent/root without a
   second unscoped lookup.
4. A user outside a private parent room gets neither direct parent/reply data
   nor list/search/saved disclosure. A user authorized only for room B cannot
   attach to a parent in room A.
5. Observer reply creation is denied; member creation succeeds; archived-room
   reply creation is rejected; removal after a history read prevents a new
   reply.

### WebSocket

1. A live root/reply frame includes canonical parent/root identifiers and the
   persisted `seq`; reconnect replay emits identical identifiers in ascending
   room sequence order.
2. Invalid parent shapes and cross-room parent ids are rejected without a
   broadcast or persisted message.
3. Observer send and nonmember handshake remain non-disclosing. A member
   socket opened before archive/removal/role downgrade is rejected by the next
   reply frame with 4003 and produces no event.
4. WebSocket pagination/replay after a cursor that lands between a root and
   reply retains the reply's `thread_id`; the client can group it without
   replaying an earlier page.

## Deterministic E2E completion

The deterministic suite must mock no message persistence or authorization
path. It will run an ASGI-backed authenticated user flow for the REST/WS
matrix above, with isolated SQLite state and a real WebSocket connection. If
Phase 2 exposes a browser reply affordance, a Playwright smoke flow additionally
creates a root, posts a reply, reloads, searches reply text, and asserts the
same thread identity. Browser tests will keep API/LLM calls locally stubbed;
real CLI/LLM behaviour remains outside E2E scope.

## Handoff trigger

Once task #18 publishes the field names, route forms, migration number, and
invalid-parent error contract, task #19 will turn this plan into executable
REST/WS regressions on task #20's exact head and report focused plus full-suite
results.
