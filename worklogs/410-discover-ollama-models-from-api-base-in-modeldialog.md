# feat(llm-gateway): discover Ollama models from api_base in ModelDialog (#410)

- Commit: `9fcf6ff` (9fcf6ff01d0d79f0f0d4a07c8402222840bac1c4)
- Author: Changyong Um
- Date: 2026-06-04T11:02:23+09:00
- PR: #410

## Situation

Registering an Ollama model in the LLM Gateway admin UI required hand-typing the `upstream_model`
field. The ModelDialog auto-fills the `ollama_chat/` prefix when Ollama is selected, but the
operator still has to append the real model name. Leaving it as the bare prefix (or fat-fingering
the id) makes litellm send an empty/invalid model to Ollama, which returns
`{"error":"model is required"}` — the failure debugged in #408. There was no way to see what models
the target Ollama actually had installed.

## Task

- Let the operator fetch the installed model list from a given `api_base` and pick from it.
- Do it without breaking when `api_base` is a server-internal address or when Ollama's CORS blocks
  the browser.
- Keep manual text entry working as a fallback (offline / remote registration).
- Scope to Ollama only; don't regress the existing CRUD/test flow.

## Action

- `packages/cluster/anygarden/api/v1/llm_gateway.py` — added `OllamaModelsRequest` /
  `OllamaModelsResult` models and `POST /api/v1/llm-gateway/ollama/models` (admin-only). It builds
  a fresh `httpx.AsyncClient(timeout=5.0)` and GETs `{api_base}/api/tags` (default
  `http://localhost:11434` when blank), extracting `models[].name`. Connection error / non-200 /
  bad JSON return `ok=false` on a 200.
- `packages/cluster/frontend/src/hooks/useLLMGateway.ts` — added `OllamaModelsResult` interface and
  a standalone `fetchOllamaModels(apiBase)` (not a method of `useGatewayModels`).
- `packages/cluster/frontend/src/components/admin-llm-gateway/ModelDialog.tsx` — Ollama-only "Load
  models" button (outline/sm) beside the api_base field, with `ollamaModels` / `loadingModels` /
  `modelsError` state. Selecting from the native `<select>` sets `upstream_model=ollama_chat/<m>`
  and `model_name=<m>`. `handleProviderChange` clears the list so a stale Ollama list can't linger
  after switching providers.
- `packages/cluster/tests/test_llm_gateway_admin_api.py` — 5 tests via a `MockTransport` helper
  (`_patch_ollama_transport`): success, default-base-when-blank, connection error, non-200,
  non-admin 403.

## Decisions

From `.tmp/plan-410-ollama-model-autolist.md` and the prior brainstorming:

- **Call path — backend proxy vs browser `fetch`**: chose a backend endpoint. Direct browser →
  Ollama breaks on Ollama's default CORS (forces `OLLAMA_ORIGINS`) and can't reach a
  server-internal api_base (docker/LAN). The gateway already routes server → Ollama for real
  inference, so list discovery must use the same path to avoid "lists fine but inference fails".
- **Error shape — `ok:false` 200 vs 4xx**: mirrored the existing `/test` handler — a
  reachable-but-failed probe is a normal outcome, returned as a 200 the dialog renders inline; only
  a hard non-200 (e.g. 403) throws in the hook.
- **`fetchOllamaModels` standalone vs on `useGatewayModels`**: standalone. ModelDialog doesn't use
  `useGatewayModels`; folding it in would have triggered that hook's `useEffect` model-list fetch
  for no reason. ModelDialog owns the request via local state.
- **Dropdown — native `<select>` vs shadcn Select**: native, matching the dialog's existing
  provider `<select>`. No `ui/select.tsx` exists; adding one for a single dialog is YAGNI.
- **Rejected (out of scope)**: vLLM/custom discovery, api_base SSRF allowlist (admin-only surface,
  low risk), and debounced auto-fetch (explicit button is simpler and avoids partial-input calls).
- **Assumptions**: Ollama `/api/tags` returns `{models:[{name}]}` (0.1+ standard); 5s timeout
  suffices for local/LAN. A slow remote or a changed response shape would trigger revisiting.

## Result

- Operators can load and pick from installed Ollama models; selection fills both fields correctly,
  eliminating the #408 empty-upstream foot-gun. Manual entry remains as fallback.
- 1011 cluster tests pass (1006 prior + 5 new); ruff clean; frontend `npm run build` (tsc) passes.
- No change to the existing model CRUD/test flow or non-Ollama providers.
