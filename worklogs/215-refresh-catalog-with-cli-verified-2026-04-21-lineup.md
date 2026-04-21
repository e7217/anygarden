# feat(engines): refresh catalog with CLI-verified 2026-04-21 lineup (#215)

- Commit: `fbb1ced` (fbb1ced94f41b263d5f9a7c71836512ec96750a5)
- Author: Changyong Um
- Date: 2026-04-21T13:34:22+09:00
- PR: #215

## Situation

`doorae.engines.catalog` powers the model + reasoning-effort dropdowns in the "Create Agent on Machine" dialog. Two things had drifted out of sync with reality since #6 (the last refresh, 2026-04-15):

- **Claude Code's default was still Opus 4.6**, even though Opus 4.7 shipped as generally-available. Gemini listed `gemini-3-pro`, which Google retired on 2026-03-09. Codex was missing `gpt-5.4-mini` and `gpt-5.3-codex-spark` that OpenAI had added.
- **The Claude Code reasoning vocabulary was wrong**: the catalog exposed `disabled/enabled/adaptive` (Messages API "extended thinking" terms), but the `claude` CLI's `--effort` flag parser rejects those values and only accepts `low/medium/high/xhigh/max`. Any value picked from the admin dropdown would not have survived the CLI's arg parser, effectively making the control inert whenever agents were launched via Claude Code.

Prior refreshes had leaned on vendor marketing docs, which this round's probing showed to be unreliable — some docs-listed IDs (`gpt-5.4-pro`) are API-key-only and 400 under ChatGPT-account auth; other CLI-binary-visible IDs (`claude-opus-4-6-fast`) aren't public aliases at all.

## Task

- Replace the Claude Code reasoning levels with the CLI's actual effort scale so what the UI offers matches what the CLI accepts.
- Re-ground every model ID with an actual `exec` round-trip under the shipped CLI versions (`claude` 2.1.116, `codex` 0.121.0, `gemini` 0.37.1). Binary symbol tables and marketing docs are advisory only.
- Keep the catalog shape stable: same `EngineCatalogEntry` fields, same REST contract, so `AdminMachines.tsx` and `/api/v1/agents/engines/{engine}/models` stay untouched.
- Leave a traceable baseline — docstring must explain *how* values were verified, so the next refresh is reproducible.

## Action

Touched two files (`packages/cluster/doorae/engines/catalog.py`, `packages/cluster/tests/test_engine_catalog.py`). Agent adapters (`packages/agent/doorae_agent/integrations/*`) and the frontend (`packages/cluster/frontend/src/hooks/useAgents.ts`, `AdminMachines.tsx`) were deliberately untouched.

**Catalog (`catalog.py:1-31` docstring, `catalog.py:55-193` dict)**

- Rewrote the header to record the 2026-04-21 verification pass and explain *why* CLI round-trip beats reading docs — specifically calls out the Codex "accepts `model=INVALID` at parse time" gotcha and Claude Code's CLI-vs-API vocabulary split.
- `codex`: default stays `gpt-5.4`; models narrowed to the ChatGPT-auth-compatible set `gpt-5.4 / gpt-5.4-mini / gpt-5.3-codex / gpt-5.3-codex-spark / gpt-5.2`. Engine-level reasoning widened to `minimal/low/medium/high/xhigh` to match the real config-validator output. Per-model `reasoning_levels` preserved so the dropdown narrows correctly for each model.
- `claude-code`: default flipped to `claude-opus-4-7`; model list trimmed to `opus-4-7 / opus-4-6 / sonnet-4-6 / sonnet-4-5 / haiku-4-5`. Per-model `reasoning_levels` removed — all models use the same effort scale under the CLI — and the engine-level list is now `low/medium/high/xhigh/max`.
- `gemini-cli`: default switched to `gemini-3-pro-preview`; model list scoped to the four IDs the `/model` picker advertises (`3-pro-preview / 3-flash-preview / 2.5-pro / 2.5-flash`). Reasoning levels stay `low/medium/high` because the adapter still maps those onto `--thinking-budget 1024/8192/32768` (`gemini_cli.py:236-241`).
- `openai` / `anthropic` (direct-API engines): defaults updated to `gpt-5.4` and `claude-opus-4-7`. `anthropic` keeps `disabled/enabled/adaptive` — that's the real Messages-API vocabulary, distinct from the CLI's effort scale.

**Tests (`tests/test_engine_catalog.py:51-61, :117-122, :155-160`)**

- `test_is_valid_reasoning_effort_engine_level`: previously asserted codex's engine-level list excluded `xhigh`. After the refresh it *does* include `xhigh`, so the "false-side" probe is now `none` (valid to the CLI, intentionally omitted from the catalog to avoid surfacing a "disable" pseudo-level).
- `test_is_valid_reasoning_effort_model_level`: swapped the "model that rejects `xhigh`" probe from the (now-present) `gpt-5.4-mini` to `gpt-5.2`, which has only `low/medium/high`.
- `test_get_codex_models`: still asserts `gpt-5.4` + `gpt-5.4-mini` are surfaced by the REST endpoint.

**Incidental cleanup (`catalog.py:34`)**

- Dropped the unused `field` import flagged by ruff; a pre-existing lint-debt line that would block CI on any future edit to this file.

## Decisions

Full rationale sits in `.tmp/plan-215-engine-catalog-2026-04-21.md` §3.2. Three calls drove the shape of the diff.

1. **Claude Code reasoning vocabulary: CLI terms, not API terms.**
   - Options weighed: (A) keep `disabled/enabled/adaptive` since that's what Anthropic docs show; (B) switch to `low/medium/high/xhigh/max` because that's what the CLI accepts; (C) dual-vocab with per-engine mapping.
   - Tipped the scale: `claude --effort disabled` fails at parse with `It must be one of: low, medium, high, xhigh, max`. The UI was shipping dropdown values the CLI couldn't consume. Since `claude-code` and `anthropic` are already separate engine entries, natural path forward is CLI vocab for the former, API vocab for the latter — which is what landed.
   - Rejected: (A) would perpetuate the silent-drop bug; (C) adds a mapping table with nothing to gain because the two engines already partition cleanly.
   - Assumption worth flagging: the Claude Code adapter (`claude_code.py`) currently ignores `reasoning_effort` entirely. Until that's wired up (out of scope for this issue), the values in the catalog are still UI-only for Claude Code. If the adapter is later extended, this vocabulary choice must remain — don't remap back to API terms at the boundary.

2. **Codex model set: ChatGPT-auth-working only.**
   - Options weighed: (A) include every ID the binary's symbol table holds; (B) only what round-trips cleanly under the machine's actual auth (ChatGPT account); (C) include all + label API-key-only variants in the UI.
   - Tipped the scale: `codex -c model=gpt-5.4-pro exec …` returns `400 invalid_request_error: "not supported when using Codex with a ChatGPT account."` The catalog is a "what can the admin pick" list, not a "what OpenAI has ever shipped" list.
   - Rejected: (A) sends users into runtime errors when they pick Pro/Codex-Max under ChatGPT auth — same class of bug #6 had to chase; (C) was rejected because no API-key user exists in this deployment and the cleaner future path is a separate `codex-api-key` engine entry rather than label-gating inside `codex`.
   - Assumption worth flagging: deployment stays on ChatGPT-account auth. If that changes, revisit — add the 5.4-pro / 5.2-codex / 5.1-codex-max / 5.1-codex-mini IDs, ideally via a second engine entry so the two sets stay distinguishable.

3. **Gemini model set: `/model` picker list, not bundle-grep list.**
   - Options weighed: (A) every string the bundle contains (`gemini-3.1-pro-preview`, `-customtools`, `-flash-lite-preview`, …); (B) only the four IDs the interactive `/model` picker shows.
   - Tipped the scale: bundle-grep surfaces fallback-routing targets and internal variants that aren't user-facing. The picker list is the CLI's own curated "what users may select" set, which is exactly what the admin dropdown should mirror.
   - Assumption worth flagging: when `gemini-3.1-pro-preview` returns to the picker (pulled in 0.37.1 per the docs), add it back. For now, `gemini-3-pro-preview` is the live Pro tier.

## Result

- `uv run pytest packages/cluster/tests/test_engine_catalog.py packages/cluster/tests/test_agents_api.py -q` — 44 passed.
- `uv run ruff check packages/cluster/doorae/engines/catalog.py packages/cluster/tests/test_engine_catalog.py` — clean.
- Admin "Create Agent on Machine" dialog now offers:
  - Claude Code → 5 models, effort dropdown `low/medium/high/xhigh/max` (matches CLI parser).
  - Codex → 5 models, per-model effort narrowing unchanged.
  - Gemini → 4 picker-visible models; adapter's thinking-budget mapping untouched.
- Existing agent records with legacy `reasoning_effort` values (e.g. `adaptive` on Claude Code) are not migrated — harmless today because `claude_code.py` doesn't consume the field. A follow-up that wires the adapter to `--effort` should include a one-time normalization.
- Follow-ups deferred: (1) Claude Code adapter consuming `reasoning_effort`, (2) per-agent Settings dialog exposing model/effort post-creation, (3) `codex-api-key` engine entry for Pro/Codex-Max variants.
