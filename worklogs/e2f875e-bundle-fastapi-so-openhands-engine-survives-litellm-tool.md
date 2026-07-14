# fix(agent): bundle fastapi so OpenHands engine survives litellm tool-calls

- Commit: `e2f875e` (e2f875e7bf122b87a5a5c847577d99108536c397)
- Author: Changyong Um
- Date: 2026-07-14T14:21:34+09:00
- PR: — (branch `fix/agent-fastapi-openhands`)

## Situation

Local-LLM agents on the `openhands` engine produced no reply. The activity
log showed `engine_call_finished · openhands · failed · Conversation run
failed for id=<uuid>: No module named 'fastapi'`. The three CLI engines
(claude-code / codex-cli / gemini-cli) were unaffected because they run as
separate CLI subprocesses; only OpenHands runs in-process as a Python SDK,
so it is exposed to the agent process's Python dependency set. The agent is
spawned via `uvx anygarden-agent` (spawner falls back to uvx when
`anygarden-agent` is not on PATH — `packages/machine/anygarden_machine/
spawner.py:1110`), and that PyPI-published environment shipped without
`fastapi`.

## Task

- Root-cause why an OpenHands turn dies with `ModuleNotFoundError: No module
  named 'fastapi'` even though openhands-sdk itself never imports fastapi.
- Make the OpenHands engine actually run in a clean-install / uvx
  environment, with the minimum necessary dependency footprint.
- Bump `anygarden-agent` for a patch release.

## Action

- `packages/agent/pyproject.toml`: added `fastapi>=0.110,<0.120` to
  `[project.dependencies]` (range pinned to match the cluster's
  `anygarden[server]` fastapi bound), with a comment explaining the litellm
  proxy-import trigger. Bumped `version` `0.11.0` → `0.11.1`.

## Decisions

Reproduced the exact failure chain (fastapi blocked, real
`conversation.run()`): openhands drives the LLM through litellm; when the
agent has *any* tools attached, `litellm.completion()` eagerly runs
`from litellm.responses.mcp.chat_completions_handler import
acompletion_with_mcp` → `litellm_proxy_mcp_handler` →
`litellm.proxy.litellm_pre_call_utils`, which does a top-level
`from fastapi import HTTPException, Request`. This import fires *before* the
conditional `_should_use_litellm_mcp_gateway` check (`litellm/main.py:4865`),
so it triggers for every tool-bearing completion even though we never take
the litellm MCP-gateway path.

Options weighed:
- **`fastapi` only (chosen)** — measured the real uvx env: `orjson` and
  `uvicorn` (the other proxy-import deps) already arrive transitively;
  `fastapi` was the *only* gap. Clean-env test confirmed litellm+fastapi
  (+transitive orjson) resolves the import chain. Footprint: +2 packages
  (fastapi, starlette), ~1 MB.
- **`litellm[proxy]`** — rejected. Measured at +54 packages / ~389 MB over
  base litellm (boto3, azure-*, numpy, polars, gunicorn, apscheduler, redis,
  rq, cryptography, …) — the entire litellm proxy *server* stack, none of
  which the agent uses. `acompletion_with_mcp` is only imported, never
  called.
- **Adapter-level `_skip_mcp_handler=True` (no new deps)** — rejected as
  infeasible: it must reach `litellm.completion()` as a top-level kwarg, but
  the adapter only calls `conversation.run()` and openhands' `litellm_extra_
  body` forwards to litellm's `extra_body` (nested, provider-bound), not
  top-level kwargs. No litellm global/env flag exists either.

Underlying cause is arguably an upstream litellm bug (a plain completion
should not import proxy-server modules); this fix declares the missing
transitive dependency rather than waiting on upstream. If a future litellm
stops importing `litellm.proxy` for non-MCP tool calls, this direct fastapi
dep can be revisited.

## Result

- OpenHands turns no longer raise `ModuleNotFoundError: No module named
  'fastapi'`. Verified end-to-end: with fastapi present, a real
  `conversation.run()` progresses past the import to the actual LLM call
  (only fails on the deliberately-dead test endpoint) — `FASTAPI-ERROR STILL
  PRESENT? False`.
- `uv lock` re-resolved (375 packages); `uv.lock` is gitignored so the only
  tracked change is `packages/agent/pyproject.toml`.
- Agent test suite: 489 passed locally.
- Reaches real users (uvx / clean install) once `anygarden-agent 0.11.1` is
  released; local dev already had fastapi via the cluster's `[server]` extra.
