# fix(agent): give Pi agents ask_peer through one shared bridge allowlist (#804)

- Commit: `8b72995` (8b729955a5c525f1385aeab99a31883c655cac18)
- Author: Changyong Um
- Date: 2026-10-07T17:19:05+09:00
- PR: #804

## Situation

Pi has no MCP client, so anygarden's own tools reach Pi agents through a local bridge (#712). The bridge registered only the tools in two hand-kept allowlists — `TOOL_NAMES`/`PROJECT_TOOL_NAMES` in `pi_self_tools.py` and `names`/`projectNames` in `pi_self_tools.mjs`. When `ask_peer` shipped (#737, #766) neither list was updated, so Pi agents were told by the roster prompt to call `ask_peer` but never had it. Peer questions from Pi agents fell back to mention tokens and skipped the #762 fan-in (draft, waiting state, single final answer); hop-2 Pi peers could not hand their requests back.

## Task

- Register `ask_peer` for Pi agents.
- Keep one source of truth for the allowlist so a new agent tool cannot be missed again.
- Keep treating server schemas as data, never as code.
- Tests: Pi `ask_peer` opens a fan-in group; a hop-2 Pi peer's ask lands in `requests`; drift between the server tool list and the bridge fails a test.

## Action

- New `packages/agent/anygarden_agent/runtime/execution/pi_self_tools.json` with `required` (10 skill/task tools) and `optional` (`ask_peer` + 11 project tools). On rebase it absorbed `claim_current_request`, `request_task_input` (#806) and `resume_project_task` (#816), which main had meanwhile added to both hand-kept lists.
- `pi_self_tools.py` loads it into `TOOL_NAMES` (required), `OPTIONAL_TOOL_NAMES` and `ALLOWED_TOOL_NAMES`; `_validate_tools` filters by `ALLOWED_TOOL_NAMES`. `PROJECT_TOOL_NAMES` removed.
- `pi_self_tools.mjs` reads the same JSON via `new URL('./pi_self_tools.json', import.meta.url)`; `projectNames` became `optionalNames`.
- `packages/agent/tests/test_execution/test_pi_self_tools.py`: new test — config and extension both register `ask_peer`, drop an unlisted name (`bash`) at config time, and the extension rejects a config containing it.
- `packages/cluster/tests/test_pi_self_tools_allowlist.py`: server `TOOL_SCHEMAS + PROJECT_TOOL_SCHEMAS` names must equal `ALLOWED_TOOL_NAMES` (no Node required).
- `packages/cluster/tests/test_pi_self_tools_bridge.py`: assertions use `ALLOWED_TOOL_NAMES`; new end-to-end test drives `ask_peer` through the real extension and server — coordinator's leased turn creates a `PeerAskGroup`/`PeerAskTarget` and a pending worker turn; the worker's own `ask_peer` returns `forwarded` and is stored in `forwarded_requests`.

## Decisions

- Options weighed: (a) bridge registers whatever the server's `tools/list` returns; (b) server marks agent-facing tools and the bridge follows the mark; (c) both bridge sides read one shared data file, plus a test pinning it to the server list.
- Chose (c). Dropping the allowlist (a) would let a server-provided name such as `bash` or `read` shadow one of Pi's built-in tools; the allowlist is a real safety boundary, not just bookkeeping. (b) adds protocol surface for no extra safety over (c) given the drift test.
- `ask_peer` is optional rather than required so an older server without it still yields a working bridge.
- The caller turn for `ask_peer` is resolved server-side from the agent's open turn (`peer_ask._open_turn`), not from turn headers, so the bridge needs no special handling beyond forwarding the call.
- Assumption to revisit: Pi loads extensions with jiti from the npm install, where `import.meta.url` points to the real file (verified with Pi 0.85.1's jiti). A bundled/SEA Pi build with virtual modules would need re-checking.

## Result

- Pi agents get `ask_peer` (verified through Pi 0.85.1's jiti loader with the pre-rebase list of 19 tools; the rebased list has 22, covered by the Node harness tests).
- Adding a server agent tool without updating `pi_self_tools.json` now fails `test_every_listed_agent_tool_is_on_the_pi_bridge_allowlist`.
- After rebase on b9159e4: agent package 725 passed; related cluster tests (bridge, allowlist, `test_mcp_ask_peer`, router transport) pass.
- Not done: live Pi agent E2E in a real room.
