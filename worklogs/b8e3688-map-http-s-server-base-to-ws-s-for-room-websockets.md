# fix(agent): map http(s) server base to ws(s) for room WebSockets

- Commit: `b8e3688` (b8e36880afc09880d47e36c5f29aca12375a3cbc)
- Author: Changyong Um
- Date: 2026-09-19T22:06:22+09:00
- PR: —

## Situation

Since the integrated node (`anygarden start`, #598) replaced the separate server + machine daemon for local operation, agents are spawned with `--server http://127.0.0.1:8001` (`packages/cluster/anygarden/node/execution.py:66` → `cluster_external_url_or_default()`). The legacy daemon instead passes the `ws://` prefix trimmed from its machine URL. `ChatClient._room_loop` appended `/ws/rooms/<id>` to the base verbatim, so under the integrated node every connect attempt failed with `isn't a valid URI: scheme isn't ws or wss`. A live agent-collaboration test on 2026-09-19 found all six local agents `actual_state=running` but holding zero TCP connections to the server: mentions and task assignments were never delivered and nothing showed up in `activity_logs`.

## Task

- Let agents join rooms regardless of whether the spawner hands them an `http(s)://` or `ws(s)://` base.
- Keep the REST helpers (which already map `ws→http`) and the legacy daemon path working unchanged.
- Add a regression test that fails on the old behavior.

## Action

- `packages/agent/anygarden_agent/client.py` (`_room_loop`): derive `ws_base` from `self._server_url`, rewriting an `http://`/`https://` prefix to `ws://`/`wss://` before composing `{ws_base}/ws/rooms/{room_id}`; `ws(s)://` bases pass through untouched.
- `packages/agent/tests/test_client.py`: new `TestWebSocketUrlScheme.test_room_loop_connects_with_ws_scheme`, parametrized over `http`, `https` (with a reverse-proxy path prefix), `ws`, and `wss` bases, capturing the URL handed to `ws_connect`. The `http`/`https` cases failed before the fix; the `ws`/`wss` cases lock in existing behavior.

## Decisions

- Options weighed:
  - Normalize in the agent client (chosen).
  - Make the integrated node pass a `ws://` base to the spawner.
  - Set `cluster_external_url` to a `ws://` URL as a config workaround.
- The client already owns the inverse mapping (`ws→http` for REST at `client.py:399/413/431`), so accepting either scheme there makes both spawn paths correct and also covers agents launched by hand with an `http` URL.
- Changing the node-side base was rejected because the same `server_url` also feeds consumer-side HTTP URLs such as the MCP endpoint (`{base}/mcp/rpc`), which would break with a `ws://` scheme. The config workaround was rejected for the same reason.
- Only the scheme prefix is rewritten (not a substring `replace`), so a path or host containing `http` is never altered.
- Revisit if the spawner protocol ever passes a dedicated WS URL separately from the HTTP base.

## Result

- Agent suite: 571 passed (including 4 new cases); `tests/test_client.py` 63 passed.
- Live verification on the integrated node: after restarting agents via `POST /api/v1/agents/{id}/stop|start`, each agent opened one WebSocket per room (pm/dev/qa: 5 each). Collaboration then worked end-to-end: mention delegation pm→dev (one hop; `MAX_PEER_DEPTH=1` blocks dev→qa by design), and orchestrator task delegation pm→dev→pm→qa via MCP `create_task`/`mark_task_status` completed in ~45s.
- Not addressed here (observed during the same test): the spawner pipes agent stdout/stderr without draining them (an unread pipe blocks the child after ~100–200KB); orchestrator fallback nomination fires even after the orchestrator delegated via `create_task`; mentions received while an agent is offline are not replayed after reconnect.
