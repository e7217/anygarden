# feat(engines): add gpt-6.1-terra to the codex-cli catalog (#996)

- Commit: `cb030a8` (cb030a8a68a25525d6c68a5bd1ebf068948e6a2a)
- Author: Changyong Um
- Date: 2026-11-09T14:24:58+09:00
- PR: #996

## Situation

codex 0.164.0 (released around 2026-11-09) added `gpt-6.1-terra` to its model
list with the same reasoning levels as the existing `gpt-6.1-sol`. The
Anygarden engine catalog is the authoritative source of which models and
reasoning levels are accepted at runtime; until this change the catalog did not
include `gpt-6.1-terra`, so the web UI could not offer it and validation would
reject it even though the backend accepted the model.

## Task

- Add `gpt-6.1-terra` to the `codex-cli` section of `ENGINE_CATALOG` in
  `catalog.py`, in codex's own list order (after `gpt-6-astra`).
- Set `reasoning_levels` to match `gpt-6.1-sol`: `("low", "medium", "high",
  "xhigh", "max", "ultra")`. Exclude `none` (backend accepts it but codex does
  not list it) and `minimal` (live call rejected it).
- Keep `gpt-6.1-sol` as the default — codex only describes terra generically,
  so there is no basis yet to prefer it.
- Update `CHANGELOG.md` and extend `test_engine_catalog.py` to cover the new
  model.

## Action

- `packages/cluster/anygarden/engines/catalog.py` (line ~137): inserted a new
  `EngineModel(id="gpt-6.1-terra", label="GPT-6.1 Terra", reasoning_levels=
  ("low", "medium", "high", "xhigh", "max", "ultra"))` after the `gpt-6-astra`
  entry. Added a block comment recording the live-verification date, codex
  version, and rationale for excluding `none` and keeping `gpt-6.1-sol` as
  default.
- `packages/cluster/CHANGELOG.md`: prepended a one-sentence entry under
  "Added" for the unreleased section.
- `packages/cluster/tests/test_engine_catalog.py`:
  - `test_is_valid_model`: added `assert is_valid_model("codex-cli",
    "gpt-6.1-terra") is True`.
  - `test_is_valid_reasoning_effort`: added `ultra` and no-`minimal` assertions
    for `gpt-6.1-terra`; added it to the `minimal`-rejected loop.
  - `TestEngineModelsEndpoint`: widened the `model_ids[:4]` slice to `[:5]` and
    inserted `"gpt-6.1-terra"` in the expected order; added `ultra in` and
    `minimal not in` assertions for `gpt-6.1-terra`.

## Decisions

- **Reasoning levels (`low`–`ultra`, no `none`, no `minimal`)**: a live
  `codex exec` call against codex 0.164.0 on 2026-11-09 accepted `low` and
  `ultra` and rejected `minimal`. The backend also accepts `none`, but codex's
  own UI does not expose it for terra, so the catalog follows codex's list to
  avoid presenting unsupported UX options. If codex later adds `none` or
  `minimal`, the catalog entry should be revisited.
- **List order (after `gpt-6-astra`, before `gpt-6-sol`)**: mirrors codex's
  own model order, keeping the catalog predictable for users who cross-reference
  the codex CLI directly.
- **Default stays `gpt-6.1-sol`**: codex's description of terra is generic and
  gives no quality or latency signal to justify switching. This assumption should
  be revisited once codex publishes more detail or benchmarks emerge.
- **Alternative considered — make terra the new default**: rejected; no evidence
  yet that terra outperforms sol for typical Anygarden workloads.

## Result

- `gpt-6.1-terra` is now a selectable model in the codex-cli engine catalog;
  the web UI can expose it and runtime validation will accept it.
- All existing tests continue to pass; new assertions confirm the model is
  valid, that `ultra` is accepted, and that `minimal` is not.
- The default model is unchanged (`gpt-6.1-sol`).
- Nothing pending; this is a catalog-only addition with no migration required.
