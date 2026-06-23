# fix(cluster): ack MCP JSON-RPC notifications with 202 so strict clients reach tools (#352)

- Commit: `bf4a8ae` (bf4a8aea002cb97ffe1b4aa04a27ed1d04a8761c)
- Author: Changyong Um
- Date: 2026-06-23T16:00:57+09:00
- PR: #352

## Situation

codex and gemini agents could not call the cluster's MCP tools (`mark_task_status`, `create_task`, …): every codex/gemini task stalled with `started_at IS NULL` and was swept to `failed (pickup_timeout)`, while claude-code worked. Issue #352 attributed the codex half to the bundled codex 0.114 binary "not supporting streamable HTTP MCP". Static analysis disproved that: the 0.114 binary embeds `rmcp-0.15` `streamable_http_client` and recognises the HTTP-MCP config keys. All engines point their MCP client at the same `POST /mcp/rpc` endpoint (`mcp_templates/merge.py` `anygarden_default_entry`), so the real question was why only codex failed against a shared endpoint.

## Task

- Make the agent-facing `/mcp/rpc` endpoint complete the MCP handshake for strict clients without breaking the lenient ones already working (claude-code / openhands).
- Preserve every existing single-shot JSON-RPC path (`initialize`, `tools/list`, `tools/call`) and the auth posture.
- Cover the fix with a regression test that does not require a live CLI.

## Action

- `packages/cluster/anygarden/mcp/router.py`: the handler fell through to `_jsonrpc_error(req_id, -32601, …)` (router.py:371) for any unrecognised method — **including JSON-RPC notifications**. Added a short-circuit right after parsing `method`/`req_id` (before the auth/session block): id-less messages or `notifications/*` methods now return `Response(status_code=202)` with an empty body. Imported `Response` and set `response_model=None` on the route (FastAPI rejects a `dict | Response` return annotation otherwise).
- `packages/cluster/tests/test_mcp_router_transport.py` (new): pins `notifications/initialized` and id-less messages → `202` + empty body; unknown *requests* (with `id`) → `-32601`; `initialize` and `tools/list` single-shot paths unchanged (still announce `mark_task_status`).

## Decisions

From `.tmp/plan-352-codex-gemini-mcp-tool-exposure.md` §3.2 (decision 1):

- **Options weighed for the codex half**:
  - (A) bump `codex-python` dependency — the issue's implied fix;
  - (B′) make `/mcp/rpc` honour the MCP Streamable HTTP transport — **chosen**;
  - (C) in-process stdio MCP bridge in the codex adapter.
- **What tipped the scale**: claude-code reaches the *same* `/mcp/rpc` and works, while codex fails — so the cause is client-strictness × server non-compliance, not the binary version. The most concrete, smallest violation is that notifications drew a `-32601` *response* instead of the spec-required `202 Accepted` + empty body; a strict `rmcp` `streamable_http_client` aborts the `initialize → notifications/initialized → tools/list` handshake on that. Fixing the shared endpoint repairs every strict engine at once with no agent redeploy.
- **Rejected**: (A) dependency bump — static evidence shows 0.114 already supports streamable HTTP, and a bump risks the `_install_parse_notification_shim`/token-harvest SDK shims; (C) in-process bridge — heavy (extra process + lifecycle) for what is a server-side spec gap.
- **Scope decision (Gemini half)**: not changed in this commit. The current `gemini_cli.py` already pins `cwd=agent_root` (#345) for project-scope `.gemini/settings.json` discovery, and this transport fix removes the handshake barrier so gemini benefits once its entry is loaded. A speculative `HOME`-redirect was deliberately NOT shipped to `main` without live confirmation (the running dev cluster on :8001 executes old `main`, and restarting it would disrupt the user's live agents/rooms). Live gemini task-assignment verification is the post-merge follow-up.
- **Minimalism**: only the notification→202 rule was implemented, not full SSE / `Mcp-Session-Id` negotiation — YAGNI until a strict client demonstrably needs more.
- **Assumption to revisit**: that the notification ACK is the sole handshake blocker for codex. If a live codex run still lists zero anygarden tools after this lands, capture the rmcp handshake and extend the transport (Accept negotiation / session id) per the plan's fallback.

## Result

- `notifications/initialized` and other id-less messages now return `202 Accepted` + empty body; unknown requests keep `-32601`; existing tool paths unchanged.
- New `test_mcp_router_transport.py` (5 tests) green; full `packages/cluster` suite passes (1219 passed, 1 deselected) — no regression.
- Codex half of #352 addressed at the transport layer; the fix is shared, so it also unblocks gemini/openhands handshakes against `/mcp/rpc`.
- Pending: live end-to-end confirmation on a running cluster built from this branch (codex + gemini DM task → `mark_task_status` flow), which requires redeploying the dev cluster — left to the operator.
