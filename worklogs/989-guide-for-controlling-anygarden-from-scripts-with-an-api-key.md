# docs(api): guide for controlling Anygarden from scripts with an API key (#989)

- Commit: `6e0c608` (6e0c6088a7d27a861000787833a92e0591f3308f)
- Author: Changyong Um
- Date: 2026-11-04T12:00:42+09:00
- PR: — (issue #989)

## Situation

#988 added personal API keys (`agu_…`), and the REST API already covers sending a message and reading replies. Nothing told an outside client how to put that together. `packages/cluster/docs/api.md` listed only a few routes and still showed a WebSocket path that no longer exists (`/ws/chat`). The OpenAPI schema reported a hardcoded version `0.2.0`.

## Task

- Write a how-to for external control: issue a key → authenticate REST/WS → pick a room and agent → send → receive the reply.
- Ship an example that cannot drift from the API, so it has to be exercised by a test.
- Make `/docs` and `/openapi.json` usable as the reference.
- Do not change API behaviour.

## Action

- `docs/external-control.md` (new, Korean like the other feature guides) covers:
  - scope table and failure codes (401/403/429)
  - REST and WS auth headers
  - mentions: `<@user:{participant_id}>`, `@everyone`, and the single-agent room needing none
  - polling with `since_seq`, and `GET /api/v1/turns/active` for "still working"
  - a WebSocket watcher and the `send` frame
  - operational notes
- `packages/cluster/examples/external_control.py` (new): httpx-only script with `list_rooms`, `agent_participants`, `send_message`, `wait_for_reply` and a `rooms`/`ask` CLI. It is outside the wheel (`packages = ["anygarden"]`).
- `packages/cluster/tests/test_external_control_example.py` (new): loads the example by path and runs it against the real app through `TestClient`.
  - Issues a write key and lists rooms and agents.
  - Sends a message, then checks that `wait_for_reply` does not return the person's own message.
  - Posts an agent reply with an `agt_` token and checks that polling returns it.
  - Checks that a WS client authenticated with the key received both messages.
  - Also checks the no-key exit code and the OpenAPI version/paths.
- `packages/cluster/anygarden/app.py`: the `FastAPI(...)` version is now `anygarden.__version__`, with a description pointing to the guide.
- `packages/cluster/docs/api.md`:
  - adds message POST and thread routes, `since_seq`/`limit`, a Turns section, and `participants` on room detail
  - corrects the WS path to `/ws/rooms/{room_id}`
  - links the guide
- `README.md` and `README.ko.md` link the guide.

## Decisions

- **Example tested end to end, not pasted snippets**: a separate script whose functions the test calls catches route or field changes. Snippets in markdown would rot silently. The test passes a Starlette `TestClient`, which is an `httpx.Client`, so the example needs no test hooks.
- **Agent reply simulated with an `agt_` token over REST**, not by inserting rows: this goes through the same write path a real agent uses. No engine is needed.
- **Polling as the primary path, WS as the optional one**: polling works from curl, cron and CI without a WebSocket library. WS is documented for low-latency watchers.
- **`/docs` stays public**: it was already exposed by FastAPI defaults. Hiding it behind a setting was considered (plan §6) and deferred. The schema exposes route shapes, not data.
- Revisit if the message API gains a dedicated "wait for reply" endpoint. The guide and example would then switch to it.

## Result

- People can follow one page to drive a room from a script. The example is guaranteed by a test to work against the current API.
- `/openapi.json` reports the real version (0.28.0 at the time).
- Full cluster suite: 4662 passed, 7 skipped.
- Next: #990 user MCP endpoint (adds a `wait_for_reply` tool on top of the same REST calls).
