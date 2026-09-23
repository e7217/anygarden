# feat(agents): configure direct endpoints at creation and discover served models

- Commits: `0432eed`, `9213bf3` (runbook)
- Author: Changyong Um
- Date: 2026-09-23
- PR: — (issue #685)

## Situation

Connecting a Pi or Codex agent to a local OpenAI-compatible server (vLLM, llama.cpp, Ollama `/v1`) already worked after #679, but it was hard to find. The create dialog asked only for the Pi provider and model. The Base URL sat behind a collapsed "Configure direct model connection" button in Settings, the provider had to be typed twice, and model IDs had to be copied from a manual `curl <base>/v1/models`. One operator edited the node's `~/.pi/agent/models.json`, which has no effect because Pi agents run with an isolated per-agent config, and then couldn't find a Base URL field at all.

## Task

- Let an admin create a Pi agent pointed at `http://<host>:<port>/v1` entirely from the create dialog, entering the provider once.
- Offer the model IDs served at `<base_url>/models`.
- Keep the probe admin-only and value-free (SSRF / secret-leak surface).
- Leave existing agents, the settings panel, and Pi runtime isolation unchanged.

## Action

- `engines/endpoints.py`: extracted `validate_base_url()` from `EndpointConfiguration` so both paths share one URL policy, and added `probe_models()` and `parse_model_list()`. The probe uses httpx with GET only, `follow_redirects=False`, `trust_env=False`, a 5 s timeout, a streamed 1 MiB cap, OpenAI `data[].id` parsing (plus `max_model_len`), deduplication, and `ModelProbeError` carrying fixed messages.
- `api/v1/engine_endpoints.py`: `POST /api/v1/engine-endpoints/models` (admin only). The body is parsed manually so nothing is echoed. It takes either a new `api_key` or `agent_id` + `credential_ref` (scoped by `credential_for`) and returns `reachable_from: "server"`.
- `api/v1/agents.py`: `AgentCreate.endpoint {base_url, api_protocol}` is validated in the handler before any row is written (422 with fixed text, or 409 without a `direct_endpoint_v1` machine) and persisted in the create transaction.
- Frontend: `lib/engineEndpoints.ts`, and a new `CreateAgentEndpointSection` in the Create Agent dialog (Base URL, protocol, auth, Load models feeding the model datalist). API keys are stored through the write-only credential endpoint and bound with `PUT /endpoint` after creation. `DirectEndpointPanel` is always shown, with a status line, Load models, and a "model not served" warning. The Overview toggle was removed.
- Server extra declares `httpx` explicitly. The runbook documents the new flow.

## Result

- Cluster 1762 tests pass (the new `test_engine_endpoint_models.py` covers success, unreachable, timeout, invalid URLs without echo, non-JSON, oversized, redirects, admin-only, stored-credential scoping, and create-with-endpoint validation). vitest 552 passes and `npm run build` is clean.
- Design choices: a secret-free endpoint in the create request, because the Pydantic 422 path would echo a key; a server-side probe as the first step, with machine-bus probing left as a follow-up if servers and machines diverge.
- Related: #687 (version diagnostics, merged) and #688 (managed Pi install).
