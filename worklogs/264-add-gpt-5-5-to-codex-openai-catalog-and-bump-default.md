# feat(engines): add GPT-5.5 to codex/openai catalog and bump default (#264)

- Commit: `50d7186` (50d7186d5666f8c0acf2b5e70d33317f80cc9e8c)
- Author: Changyong Um
- Date: 2026-04-25T21:22:30+09:00
- PR: #264

## Situation

GPT-5.5 was announced on 2026-04-25. The Doorae engine catalog (`packages/cluster/doorae/engines/catalog.py`) is the static source of truth that admin UIs query when offering model choices for new agents, and both the `codex` and `openai` entries still defaulted to `gpt-5.4`. The `CodexAdapter` fallback (`packages/agent/doorae_agent/integrations/codex.py:171`) likewise used `gpt-5.4`. New agents created after the announcement should default to GPT-5.5.

## Task

- Add `gpt-5.5` as a selectable model under both `codex` and `openai` catalog entries
- Bump `default_model` to `gpt-5.5` for both entries
- Bump the `CodexAdapter._model` fallback to `gpt-5.5`
- Update tests asserting the default model
- Document that the GPT-5.5 entry is announcement-only — not yet round-tripped against the codex CLI under ChatGPT-account auth — so future maintainers know the rollback path
- Do not touch LLM gateway tests or DB migration comments that incidentally reference `gpt-5.4`

## Action

- `packages/cluster/doorae/engines/catalog.py`:
  - Header docstring: bumped `Last refreshed` from 2026-04-21 to 2026-04-25 and added an explicit "Exception" paragraph noting GPT-5.5 lacks runtime verification and the rollback target is `gpt-5.4`
  - `codex` entry: prepended a new `EngineModel(id="gpt-5.5", ..., reasoning_levels=("minimal","low","medium","high","xhigh"))` with an inline comment flagging verification status, set `default_model="gpt-5.5"`
  - `openai` entry: prepended `EngineModel(id="gpt-5.5", label="GPT-5.5")` with the same inline comment, set `default_model="gpt-5.5"`
- `packages/agent/doorae_agent/integrations/codex.py:171`: `model or "gpt-5.4"` → `model or "gpt-5.5"`
- `packages/cluster/tests/test_engine_catalog.py`: `test_get_codex_models` now asserts `default_model == "gpt-5.5"` and that `gpt-5.5` is in `model_ids` (kept the existing `gpt-5.4` / `gpt-5.4-mini` assertions since both remain available)
- `packages/agent/tests/test_integrations/test_codex.py:64`: `test_default_model` now asserts `gpt-5.5`

## Decisions

Sourced from `.tmp/plan-264-gpt-5-5-default.md` §3.2.

**① Variant scope — added `gpt-5.5` only**
- Considered: (A) `gpt-5.5` only, (B) `gpt-5.5` + `gpt-5.5-mini`, (C) plus codex variants like `gpt-5.5-codex`
- Initial draft chose B for symmetry with the 5.4 series; user feedback corrected this — GPT-5.5-mini was not part of the announcement
- Tipped toward (A): catalog principle is "verified, safe options" — listing a model ID that doesn't exist would surface as a runtime failure when a user picks it (Codex does no client-side validation). Same reasoning excluded (C) — past variants like `gpt-5.4-pro`, `gpt-5.2-codex` are intentionally omitted because they're rejected under ChatGPT-account auth
- Assumption to revisit: if/when GPT-5.5-mini or a `-codex` variant is announced and verified, add it as a follow-up PR

**② Default promotion — bumped both engines and the adapter fallback**
- Considered: (A) add as option only, keep 5.4 default; (B) bump default to 5.5
- Chose (B) per user request. Blast radius is intentionally small — only the *initial value* of new agents' model field. Existing agents persist their selected model in the DB and are unaffected
- Rolled the `CodexAdapter._model` fallback into the same change for consistency, even though the fallback path (called with `model=None`) is rarely hit — keeping fallback ≠ catalog default would be a future surprise

**③ Reasoning levels — assumed identical to 5.4**
- Used `("minimal","low","medium","high","xhigh")` matching the 5.4 entry, on the assumption GPT-5.5 inherits 5.4's reasoning taxonomy
- The catalog doesn't enforce this client-side anyway; if a level is rejected at runtime it surfaces as a config error from the codex CLI validator. Cheap to narrow in a follow-up PR if needed

**④ Header comment — added explicit "Exception" paragraph**
- Considered: (A) just bump the date; (B) date + dedicated exception paragraph
- Chose (B) so the verification gap is visible to anyone reading the catalog. The header otherwise emphasizes a "verified by round-tripping" principle, and silently adding an unverified entry would erode that contract
- Trigger for revisiting: once GPT-5.5 is confirmed working under ChatGPT-account auth via `codex exec`, drop the inline `# announcement-only` comments and remove the exception paragraph

**Rejected explicitly**: touching `packages/cluster/doorae/db/models.py` and `db/migrations/versions/012_agent_model.py` where `gpt-5.4` appears as a docstring example — they're illustrative, not behavioral. Touching them would expand the diff for zero functional benefit.

## Result

- Cluster catalog tests: 11 passed (`packages/cluster/tests/test_engine_catalog.py`)
- Agent codex adapter tests: 23 passed (`packages/agent/tests/test_integrations/test_codex.py`)
- Full cluster suite: 733 passed
- Full machine suite: 313 passed
- Full agent suite: 1 pre-existing failure (`test_openai.py::test_integrate_registers_handler` fails on `main` too — `OPENAI_API_KEY` env-var requirement, unrelated)
- Lint: changed files clean; one pre-existing ruff warning in unrelated test setup left untouched
- Pending: runtime verification of `gpt-5.5` against `codex exec` with ChatGPT-account auth. If it fails, the rollback is a one-line revert of `default_model` per the header note
