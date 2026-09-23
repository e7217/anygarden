# fix(engines): offer GPT-6 in the codex-cli catalog and drop rejected GPT-5.x ids (#692)

- Commit: `203e2b0` (203e2b0596f835939525f820f1799eba75108077)
- Author: Changyong Um
- Date: 2026-09-23T16:00:25+09:00
- PR: #692 (issue)

## Situation

OpenAI shipped GPT-6 Astra on 2026-09-04 and GPT-6 Sol and Luna on 2026-09-22. The codex-cli model catalog still topped out at GPT-5.6 and defaulted to `gpt-5.6-terra`, which codex's own model list now labels "Older balanced model". The catalog also offered five models that the Codex backend no longer accepts for ChatGPT-account logins. We found this while anygarden-agent 0.13.0 (#637) was waiting to be tagged. The adapter fallback default lives in the agent package, so the fix was folded into that release.

## Task

- Offer the GPT-6 models, each with the reasoning levels the backend actually accepts for it.
- Stop offering model IDs that fail on the first turn.
- Move the default off a tier codex now calls "Older".
- Keep the server catalog and the agent adapter fallback in sync.
- Do not break agents that are already pinned to a removed model.

## Action

Live verification on codex-cli 0.155.1 with a ChatGPT-account login, done before editing:
- `codex exec -m gpt-6-astra|sol|luna` answered `OK` at `low` and at the top level (`ultra` for astra/sol, `max` for luna).
- The backend's own error text shows that `minimal` is rejected for all three GPT-6 models. Its accepted-value lists are: astra `low…max`; sol and luna `none, low…max`.
- `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.3-codex`, `gpt-5.3-codex-spark` and `gpt-5.2` each returned 400 "not supported when using Codex with a ChatGPT account".
- `~/.codex/models_cache.json` (fetched the same day) lists astra/sol with `low…ultra`, luna with `low…max`, GPT-5.6 as "Older" and 5.5 as "Legacy".

Code changes:
- `packages/cluster/anygarden/engines/catalog.py`:
  - Added `gpt-6-astra`, `gpt-6-sol` and `gpt-6-luna` at the top of the codex-cli list, with per-model levels taken from the codex cache.
  - Added `ultra` to the engine-level levels.
  - Removed the five rejected IDs.
  - Changed `default_model` from `gpt-5.6-terra` to `gpt-6-sol`.
  - Rewrote the provenance comment to record the verification date and the reason for each removal.
- `packages/agent/anygarden_agent/integrations/codex_cli.py:160`: the fallback model is now `gpt-6-sol`.
- Tests:
  - `packages/cluster/tests/test_engine_catalog.py` now covers the new default, GPT-6 ordering, `ultra` for astra/sol but not luna, the rejection of `minimal` on GPT-6, and a new check that the rejected IDs are not offered.
  - Updated the default-model expectation in `packages/agent/tests/test_room_execution.py` and `packages/agent/tests/test_integrations/test_codex_cli.py`.
- CHANGELOG entries: cluster `Unreleased → Changed`, agent `v0.13.0 → Changed`.

## Decisions

- **Default `gpt-6-sol` rather than `gpt-6-astra`.** The existing policy was to default to the balanced tier (terra for GPT-5.6) for cost and quality, and let operators opt into the flagship for each agent. Codex describes sol as "Workhorse model for coding and everyday work", which fits that slot; astra is "frontier intelligence for the most demanding work". Luna was rejected as the default because it is the cheap/fast tier.
- **Reasoning levels come from the codex model cache, not from the backend error text.** The two sources disagree. The backend lists `none` for sol and luna and has no `ultra`. Codex offers `ultra`, and `codex exec … -c model_reasoning_effort=ultra` succeeded, so codex evidently maps it on the client side. The catalog values are passed straight to codex, so codex's accepted set is the right contract. `none` stays omitted, following the catalog's existing rule of not surfacing a "disabled" pseudo-level. `minimal` is excluded from GPT-6 because the backend rejects it outright.
- **Removed the five IDs rather than marking them deprecated.** They do not degrade; they fail. Offering them only lets an admin configure an agent that errors on its first turn. Agents already pinned to one keep their stored value: the server does not re-validate on start, and `OverviewPanel.tsx` renders an unlisted value as "Current: … (no longer in catalog)". No data migration was needed.
- **Kept GPT-5.6 and 5.5.** They still answer, and agents may be pinned to them deliberately.
- **Left the engine-smoke model (`gpt-5.6-sol`) alone.** It still works, and changing it is unrelated to the catalog. Revisit this if GPT-5.6 is retired.
- **Assumption to revisit:** the verification used a ChatGPT-account login. API-key logins may accept a different set of IDs, including the removed ones. This catalog has always been keyed to the ChatGPT-account behaviour.

## Result

- The admin UI offers GPT-6 Astra, Sol and Luna. New agents with no model pinned run on `gpt-6-sol`. `ultra` is selectable for astra and sol.
- Models that the backend rejects can no longer be selected.
- Tests: `packages/cluster/tests/test_engine_catalog.py` 19 passed; the full cluster suite 1765 passed; `packages/agent` 529 passed.
- Pending: PR, merge, and inclusion in the anygarden-agent 0.13.0 tag. That tag is still blocked on the `release-smoke` OpenAI key (`AUTH_REJECTED`).
