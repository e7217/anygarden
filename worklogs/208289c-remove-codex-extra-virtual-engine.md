# refactor(engines,llm-gateway): remove codex-extra virtual engine

- Commit: `208289c` (208289c709c9f305b952b978f57f0a62bc839009)
- Author: Changyong Um
- Date: 2026-04-25T14:53:09+09:00

## Situation

`codex-extra` (introduced in #254) was the only virtual engine in the catalog and existed for one purpose: route codex CLI traffic through the embedded LiteLLM gateway so admins could point a codex-style agent at non-OpenAI upstream models. Six investigation cycles against agent01-extra (codex-extra + Ollama `qwen3.6:27b`) showed the round-trip is structurally broken at multiple layers:

- The codex CLI 0.114 binary speaks the OpenAI Responses API with separate `output_text.delta` and `function_call_arguments.delta` channels plus a `reasoning` field. LiteLLM's Ollama provider does not translate native tool calling into those channels — qwen returns the tool call as plain JSON inside `output_text`, and codex's app-server hangs waiting for the tool channel that never arrives.
- A separate diagnosis identified an auth-cache poisoning vector: the spawner symlinks `~/.codex/auth.json` into `<agent_root>/.codex/` for plain codex agents, and codex-extra's sentinel-substituted `OPENAI_API_KEY` (the agent's own doorae token) caused codex CLI to write a fresh `auth.json` that followed the symlink and clobbered the host file with a useless agent token. Plain codex agents on the same host then 401'd at every request.

The fix path of "patch each leg" was rejected as architecturally unsound — codex CLI's response API and tool-channel assumptions are not reconcilable with arbitrary LiteLLM-routed upstreams. For routing local Ollama models, `openai` or `openhands` engines are the right primitives.

## Task

Remove the `codex-extra` engine and the scaffolding that supports only it (sentinel substitution, gateway-secrets builder, virtual-engine plumbing, host-auth symlink interaction, frontend label/UI). Keep the LLM Gateway feature itself — admin tooling and direct curl access remain valid use cases — only remove the agent-side enrolment that was unique to codex-extra.

## Action

- `packages/cluster/doorae/engines/catalog.py`: dropped the `codex-extra` `EngineCatalogEntry`, `VIRTUAL_ENGINE_TO_BASE`, `base_engine()`, and `is_gateway_engine()`.
- `packages/cluster/doorae/engines/__init__.py`: removed the corresponding re-exports.
- `packages/cluster/doorae/scheduler/lifecycle.py`: removed `_build_gateway_engine_secrets`, `_http_base_url`, `AGENT_TOKEN_SENTINEL`, the `llm_gateway_enabled` and `server_url` constructor parameters, and every `base_engine(agent.engine)` call (resolved to plain `agent.engine`). The sync frame's `engine_secrets` is now always `{}`.
- `packages/cluster/doorae/app.py`: dropped the `server_url` and `llm_gateway_enabled` arguments to `AgentLifecycle(...)`.
- `packages/cluster/doorae/api/v1/agents.py`: removed `is_gateway_engine` import and the `LLMGatewayModel` lookup branch in `get_engine_models` (gateway models no longer surface through `/engines/{engine}/models`).
- `packages/cluster/doorae/api/v1/machines.py`: removed the supervisor-state-driven `codex-extra` augmentation in `list_machine_engines`.
- `packages/cluster/frontend/src/components/AdminMachines.tsx`: dropped the `codex-extra` label and its model-empty / route-through-gateway hint paragraphs.
- `packages/machine/doorae_machine/spawner.py`: removed `AGENT_TOKEN_SENTINEL` and `_expand_agent_token_sentinel`. The stdin payload is now `json.dumps(dict(msg.engine_secrets or {}))` directly.
- `packages/agent/doorae_agent/integrations/codex.py`: removed the `_OPENAI_SDK_ENV_KEYS` constant, the `agent_secrets` import, and the `secrets_in_env(...)` block that wrapped `Codex()`. Construction is plain `self._codex = Codex()`.
- Deletions: `packages/cluster/tests/test_llm_gateway_manifest_injection.py` (codex-extra sentinel/secret unit tests), `packages/machine/tests/test_gateway_sentinel.py` (sentinel substitution tests). Updated `test_engine_catalog.py` to drop the virtual-engine carve-out, `test_daemon.py` to use a `claude-code` agent shipping `ANTHROPIC_API_KEY` instead of the codex-extra sentinel, and removed `TestCodexEnvInjection` from `test_llm_gateway_env_injection.py`.
- Database: deleted the lone `agent01-extra` row (`engine='codex-extra'`) so the next sync doesn't reference an unknown engine.

## Result

Single PR removes 511 lines (13 files modified, 2 deleted). `packages/agent` 284 pass, `packages/cluster` 733 pass, `packages/machine` 306 pass on the resulting state (the one `test_openai.py` failure is pre-existing on `origin/main` and unrelated). Ruff lint counts unchanged from baseline (13/91/22). Frontend `npm run build` clean. With this commit and `180eb5c` together on `refactor/remove-codex-extra-engine`, the host's `~/.codex/auth.json` is no longer reachable for write through any engine path, the spawn manifest carries through engine-correctness fixes for any future engine that uses `engine_secrets`, and codex remains a plain host-auth engine.
