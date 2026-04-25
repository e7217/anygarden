# fix(machine,agent): cache engine_secrets in-memory and forward model/effort to codex SDK

- Commit: `180eb5c` (180eb5c2f8561b9af9cf8d996c6edfa0095b0450)
- Author: Changyong Um
- Date: 2026-04-25T14:48:18+09:00

## Situation

Investigating why agent01-extra (a `codex-extra` agent pointed at `qwen3.6:27b` via the embedded LiteLLM gateway) never posted a reply to its DM, three engine-level bugs surfaced that affect more than just the codex-extra path:

1. The machine daemon's `ManifestStore.save()` strips `engine_secrets` before writing to disk (a security property), but `_reconcile_agent` then loads the manifest back from disk to build the spawn manifest — so any frame-supplied secrets were lost in the round trip and the agent stdin received `{}`. Any engine that ever ships values via `engine_secrets` (the new `claude-code-extra` slot, future Anthropic gateway routing) would hit the same data loss.
2. `CodexAdapter.start()` was constructing `Codex()` with no options. The codex SDK's `Codex.__init__` does not actually spawn the app-server subprocess — `start_thread()` does, on the first message — so the empty `CodexOptions()` froze in at construction time. The SDK then defaulted to `gpt-5.4` at `xhigh` effort regardless of what `--model` / `--reasoning-effort` the agent was spawned with. Captured `/v1/responses` request bodies confirmed `model: "gpt-5.4"` even though the manifest carried `qwen3.6:27b`.
3. `make server-dev` invoked uvicorn with `--port $(DEV_PORT)` (8001) but never exported `DOORAE_PORT`. `DooraeSettings().port` therefore stayed at the default `8000`, and the server-derived `server_url` (used to compose downstream URLs) pointed at a port nothing was listening on.

## Task

Land the three engine-correctness fixes as a single bundle, independent from the codex-extra removal that follows in the next commit. The fixes have to keep working for any future engine that uses `engine_secrets` or that routes through the codex SDK.

## Action

- `packages/machine/doorae_machine/manifest_store.py`: added `_secrets_cache: dict[str, dict[str, str]]` populated on `save(frame)` and exposed via `get_secrets(agent_id)`. `delete()` clears the entry. The cache is process-local — disk persistence remains stripped, so the security property is preserved.
- `packages/machine/doorae_machine/daemon.py`: `_request_token_and_spawn` now builds `SpawnManifest.engine_secrets` from `self._manifest_store.get_secrets(agent_id)` instead of the disk-loaded manifest's empty dict.
- `packages/machine/tests/test_manifest_store.py`: added a `TestManifestStoreSecretsCache` class covering save→get round-trip, empty-before-save, overwrite semantics, defensive copy on read, and clear-on-delete.
- `packages/machine/tests/test_daemon.py`: added `test_sync_forwards_engine_secrets_to_spawn` as a regression guard — sends a sync frame with `engine_secrets={"ANTHROPIC_API_KEY": "sk-test-secret"}`, drives the token grant, and asserts the spawn call's `engine_secrets` arrived intact.
- `packages/agent/doorae_agent/integrations/codex.py`: imported `TurnOptions`, stored `self._turn_options_cls`, and wired the agent's `model` / `reasoning_effort` through `ThreadStartOptions(model=...)` on first `start_thread()` plus `TurnOptions(model=..., effort=...)` per `run_text()` call. Tests use the `turn_options` keyword so existing positional assertions still match.
- `packages/agent/tests/test_integrations/test_codex.py`: extended the timeout-test fakes (`slow_run_text`, `fast_run_text`) to accept the new `turn_options=None` parameter so the asserted call shape stays clean.
- `packages/cluster/Makefile`: `server-dev` now prefixes the uvicorn invocation with `DOORAE_PORT=$(DEV_PORT)`, keeping CLI flag and settings-side port in sync.

## Result

Verified by direct probe: after the fix, codex app-server requests reach LiteLLM with `model: "qwen3.6:27b"` and the agent process listens on the expected port. Test suites: `packages/agent` 284 pass, `packages/cluster` 733 pass, `packages/machine` 306 pass (one pre-existing `test_openai.py` failure unrelated to this change). Ruff lint counts unchanged from `origin/main` baseline (13/91/22). Total diff +144 / −6 across 7 files.
