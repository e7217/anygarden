# feat(engines): add GPT-6.2 Sol/Astra and make gpt-6.2-sol the codex default (#1055)

- Commit: `317b708` (317b708c7cff7335f2de6dd2318f171a3984f40b)
- Author: Changyong Um
- Date: 2026-11-19T14:11:20+09:00
- PR: #1055

## Situation

OpenAI released GPT-6.2 Astra and Sol on 2026-11-17. The codex-cli catalog topped out at `gpt-6.1-sol` (default), `gpt-6-astra` and `gpt-6.1-terra`. The local codex 0.164.0 model list didn't include 6.2 yet but already labeled `gpt-6.1-sol` and `gpt-6-astra` as "Previous generation", so codex was showing 6.2 only to newer clients.

## Task

- Confirm the 6.2 model ids, order and reasoning levels from codex's own list rather than guessing from press coverage.
- Add `gpt-6.2-sol` and `gpt-6.2-astra` to the codex-cli catalog.
- Make `gpt-6.2-sol` the default in both the catalog and the adapter fallback. The user chose this, following the #767 precedent.
- Keep the older models for agents already pinned to them.

## Action

- Checked with codex 0.170.0 (`codex debug models`). The list order is `gpt-6.2-sol`, `gpt-6.2-astra`, `gpt-6.1-terra`, `gpt-6.1-sol`, `gpt-6-astra`, … and both 6.2 models list `low…ultra`.
- Ran live `codex exec` calls with a ChatGPT login. On 0.170.0, `low` and `ultra` passed for both models and `minimal` was rejected ("Supported values are: low, medium, high, xhigh, max"). Codex 0.164.0 rejected `gpt-6.2-sol` with "not supported when using Codex with a ChatGPT account" (`model_not_found`).
- `packages/cluster/anygarden/engines/catalog.py`: added both models, reordered the GPT-6 entries to codex's list order, set `default_model="gpt-6.2-sol"`, and added a comment block recording the verification and the codex version requirement.
- `packages/cluster/anygarden/agent/integrations/codex_cli.py:255`: changed the adapter fallback to `gpt-6.2-sol`.
- Tests: `tests/test_engine_catalog.py` covers validity, `ultra`/`minimal`, API order and default, and per-model levels. Updated the default expectations in `tests/agent/test_room_execution.py` and `tests/agent/test_integrations/test_codex_cli.py`.
- `packages/cluster/CHANGELOG.md`: Added entry, plus a Changed entry that tells operators to update Codex CLI to 0.170.0 or later first.

## Decisions

- Default model options: (a) switch to `gpt-6.2-sol`, (b) keep `gpt-6.1-sol` and only add the models, (c) `gpt-6.2-astra`. (c) costs 4× more ($8/$40 vs $2/$10 per M tokens), and the default has been the workhorse tier since GPT-6. The user picked (a) because codex already calls 6.1-sol "previous generation", the same reasoning as #767.
- The live check found that codex 0.164.0 rejects the 6.2 ids. Agents on the default model store `model=None` and resolve through the adapter fallback, so after the upgrade they would fail on machines still running the old codex. The tradeoff was put to the user again, and they kept (a). They also asked to update the local codex to 0.170.0. The requirement is documented in the CHANGELOG instead of building a version-aware fallback, which would need machine-reported codex versions to drive model choice. That was out of scope.
- Reasoning levels mirror codex's list. The backend error text omits `ultra`, but codex lists it and `ultra` calls succeeded.
- The models follow codex's list order (as in #997), which also keeps the default first.
- Revisit if codex starts requiring a newer client than 0.170.0, or if the backend starts refusing older ids (as in #692).

## Result

- The codex-cli catalog offers `gpt-6.2-sol` (default) and `gpt-6.2-astra`, and older models remain available.
- Catalog/adapter tests: 88 passed; full cluster suite 4747 passed, 7 skipped.
- Pending: machines on codex < 0.170.0 must update before default-model agents run. The exact minimum version between 0.165 and 0.169 is untested. No live end-to-end agent turn on a node was run.
