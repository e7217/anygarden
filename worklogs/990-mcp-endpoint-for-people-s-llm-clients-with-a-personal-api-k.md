# feat(mcp): MCP endpoint for people's LLM clients with a personal API key (#990)

- Commit: `fa9ead4` (rebased onto #992; originally feadab9)
- Author: Changyong Um
- Date: 2026-11-04T12:13:38+09:00
- PR: — (issue #990)

## Situation

Anygarden already exposed `/mcp/rpc` for agent tokens, but personal users who wanted to drive Anygarden from an LLM client (e.g. Claude Code) had no MCP server they could point at. The two identity types — agent tokens and personal API keys (`agu_…`, added in #988) — carry different permission models and must be strictly separated to avoid privilege confusion. No MCP tooling existed for the people-facing access path.

## Task

- Provide `POST /mcp/user`: a tools-only Streamable-HTTP JSON-RPC endpoint that accepts only personal API keys (`agu_…`), mirroring the framing of `/mcp/rpc`.
- Refuse agent tokens and sessions at `/mcp/user`; refuse personal keys at `/mcp/rpc`.
- Expose six tools: `list_rooms`, `get_room`, `read_messages`, `send_message`, `wait_for_reply`, `list_tasks`.
- Each tool must delegate entirely to the REST API in-process (same `Authorization` header), so permissions, key scope and side effects remain consistent with the REST layer.
- Authenticate the caller via `GET /auth/me` on every request so key validation, revocation and wrong-key limiting run through REST exactly.
- Extract shared JSON-RPC framing (body parsing, notification acking, `tools/call` param parsing) into `mcp/jsonrpc.py` to avoid duplication between the two endpoints.
- Reference ticket: AUTH-16.

## Action

- **`packages/cluster/anygarden/mcp/jsonrpc.py`** (new, 70 lines): extracted shared JSON-RPC helpers — `read_call` (parses body, returns 202 for notifications), `tool_call_params` (validates `tools/call` params), `jsonrpc_ok`, `jsonrpc_error`, `PROTOCOL_VERSION`.
- **`packages/cluster/anygarden/mcp/router.py`** (refactored, −51 lines): replaced inline JSON-RPC logic with calls to `jsonrpc.py` helpers; `_jsonrpc_error`/`_jsonrpc_ok`/`PROTOCOL_VERSION` removed and imported from the shared module.
- **`packages/cluster/anygarden/mcp/user_router.py`** (new, 84 lines): `POST /mcp/user` handler — checks token prefix is `agu_`, authenticates via `/api/v1/auth/me` through `internal_client`, then dispatches `initialize`, `tools/list`, `tools/call`, `ping`; returns `instructions` string on `initialize` to guide the LLM.
- **`packages/cluster/anygarden/mcp/user_tools.py`** (new, 271 lines): tool schemas and dispatcher for all six user-facing tools; `internal_client()` builds an `httpx.AsyncClient` with an `ASGITransport` backed by the live app and the caller's header; `wait_for_reply` polls in a loop (default 60 s, max 120 s, 2 s interval) then reports whether an agent is still working if nothing arrived.
- **`packages/cluster/anygarden/app.py`** (2 lines): wired `user_router.router` into the FastAPI app.
- **`docs/requirements.md`** (+1 line): added AUTH-16.
- **`docs/external-control.md`**: new section 5 — connecting Claude Code (`claude mcp add --transport http … --header "Authorization: Bearer …"`) and the tool table. **`packages/cluster/docs/api.md`**: MCP section listing `/mcp/rpc` and `/mcp/user`.
- **`packages/cluster/tests/test_mcp_user_router.py`** (new, 208 lines): integration tests covering key-type gate (agent token → 403, personal key → pass), `initialize`, `tools/list`, `tools/call` for each tool, `wait_for_reply` timeout path, and 202 ack for notifications.

## Decisions

- **Separate router over a single unified endpoint**: keeping `/mcp/rpc` and `/mcp/user` as separate routers with explicit token-type checks prevents any accidental bleed between agent and person capabilities. A single endpoint with an `if/else` on token type was considered and rejected — it would couple the two permission models and make future divergence (e.g. streaming for agents) harder.
- **In-process `httpx.ASGITransport` over direct DB calls**: tools call REST handlers in-process rather than the database layer directly. This ensures that all middleware (auth, rate-limiting, WS fan-out) runs for tool calls exactly as it does for real HTTP calls. The tradeoff is minor serialization overhead on the tool path; the correctness guarantee outweighs it.
- **`wait_for_reply` polling over WebSocket**: MCP's Streamable-HTTP transport is request/response; a true push would require SSE or WS. Polling at 2 s intervals up to 120 s (then report + re-invoke) fits within typical MCP client timeouts and avoids a persistent connection. If sub-second latency becomes a requirement, the approach would need revisiting.
- **Authenticate on every `/mcp/user` request via `GET /auth/me`**: rather than caching the resolved user, every request re-validates through the REST layer. This ensures revocation takes effect immediately at the cost of one extra in-process request per call. Acceptable given the low call rate from LLM clients.

## Result

- `POST /mcp/user` is live; personal API key holders can configure it in Claude Code (or any MCP-capable client) to interact with Anygarden rooms and tasks.
- Agent tokens and sessions are hard-rejected at `/mcp/user`; personal keys are hard-rejected at `/mcp/rpc`.
- Shared JSON-RPC framing is extracted, reducing duplication between the two endpoints.
- `tests/test_mcp_user_router.py` (9 tests) covers auth gating, all six tools, and the timeout/still-working path of `wait_for_reply`.
- AUTH-16 added to `docs/requirements.md` and linked to the tests.
- Live check: against a scratch server, the official `mcp` Python SDK (1.12.4, strict Streamable HTTP client) completed initialize, listed the six tools, listed rooms and sent a message with a write key.
- Full cluster suite: 4668 passed, 7 skipped. `/mcp/rpc` tests unchanged after the framing extraction (126 MCP tests passed).
