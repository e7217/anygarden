# Agent activity history

The agent settings Activity section shows stored lifecycle events grouped by
`request_id`. Loading, empty history, and failed requests are separate states.
Refresh and failed-page retry preserve previously loaded records. Older pages
remain available while in-progress requests receive new events.

## API contract

`GET /api/v1/agents/{agent_id}/activity` remains an **array** of `ActivityLogOut`.
Existing callers with no cursor retain latest-first behavior and a default
`limit=50`. Access remains restricted to system administrators.

| Query | Meaning |
| --- | --- |
| `limit` | Integer from 1 to 200, inclusive; default 50 |
| `outcome`, `engine` | Existing optional exact-match filters |
| `before_timestamp` + `before_id` | Older rows, ordered by `(timestamp DESC, id DESC)` |
| `after_timestamp` + `after_id` | Newer rows, ordered by `(timestamp ASC, id ASC)` |

A cursor is an exclusive `(timestamp, id)` boundary scoped to the requested
agent and filters. Supply both members of a pair. Before and after pairs cannot
be combined. Invalid limits, incomplete pairs, invalid timestamps, and timestamps
without an offset return HTTP 422. IDs are at most 36 characters; `before_id`
must be nonempty. Returned timestamps use UTC with six fractional digits and
`+00:00`, preserving database precision. Pass the returned timestamp through
unchanged using URL encoding rather than converting it through a millisecond
JavaScript Date.

For older pages, take the last retained row from the previous page. Clients may
request `page_size + 1` rows (within the limit) to detect another page, retain only
`page_size`, and use the last retained row as the next boundary. The extra row
will be returned again on the next request and must not become the cursor.

For incremental refresh, `after_id=` (empty) with the newest known timestamp
replays all rows at that exact timestamp. Deduplicate by event ID. This includes
same-time arrivals whose IDs sort below an already displayed event. Follow-up
pages use the full last-returned `(timestamp, id)` cursor and ascending order;
continue until a page contains fewer than the requested limit. This catches up
bursts larger than one page without moving the independent older-history cursor.
A deleted boundary row does not invalidate a cursor: comparisons use its values,
not a lookup by ID. Events are append-only; changing historical timestamps or
inserting backdated events before the refresh boundary requires a history reload.

## Frontend lifecycle

The panel fetches 50 visible events plus one lookahead for history, and refreshes
in batches of 200. Each refresh performs at most five requests; a remaining
continuation resumes later, so a continuously growing stream cannot trap a single
request loop. Received events are merged by ID in stable descending time/ID order.

Automatic refresh runs every five seconds only while there is an unfinished
request or an unfinished catch-up batch, the section is expanded, and the browser
tab is visible. It stops on errors until the user retries. A completed request is
recognized by `handler_finished` or `handler_orphaned`; receiving a response alone
does not prematurely stop the final outcome check. Idle histories can be refreshed
manually, and opening the section refreshes an existing snapshot.

Changing agents, collapsing the section, or unmounting cancels pending transport
requests. A generation check after JSON decoding also discards responses when a
transport ignores cancellation. An A → B → A transition does not reuse the old
A request. The previous agent's records are hidden immediately.

Event pagination may split a request across pages. Loading older activity merges
those events into the existing request group; the UI does not claim a page is a
complete execution trace.

A `handler_finished` event with `queued` or `retrying` is an intermediate result. It keeps the request visible as pending and does not stop polling. Only `ok`, `failed`, `timeout`, `cancelled`, `rejected`, `retry_exhausted`, a legacy finish without an outcome, or `handler_orphaned` closes a request. Events and turns with equal timestamps use their event IDs for deterministic ordering.
