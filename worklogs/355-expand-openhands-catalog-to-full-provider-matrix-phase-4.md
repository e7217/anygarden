# feat(cluster,agent): expand OpenHands catalog to full provider matrix (Phase 4) (#355)

- Commit: `ee2ff9a`
- Author: Changyong Um
- Date: 2026-05-09
- PR: #355

## Situation

Phase 0 deliberately seeded the openhands engine catalog with one
model per provider (`anthropic/claude-opus-4-7`,
`openai/gpt-5.4`, `gemini/gemini-3-pro-preview`) — three entries
total — as a smoke-test surface so the adapter could be validated
without flooding the admin UI with options that hadn't been wired
or verified yet. After Phases 1–3 (MCP wiring, skills awareness,
DelegateTool) the adapter has the full plumbing to drive any
litellm-supported provider. The remaining migration step before
validation (Phase 5) is making the model menu match what
operators already see for the three CLI engines, so switching
from `claude-code` to `openhands` doesn't silently shrink their
choice surface.

## Task

Expand the openhands catalog to the same provider × model matrix
the dedicated CLI engines advertise, while preserving:

- Per-model `reasoning_levels` narrowing — Anthropic doesn't accept
  `minimal`, Gemini doesn't accept `xhigh` / `max`, etc. The admin
  UI uses the per-model list when present so it shouldn't surface
  knobs the underlying provider would reject at runtime.
- The litellm `provider/model` prefix, since `openhands.sdk.LLM`
  routes on it directly. No separate provider field — encoding it
  in the model id keeps the existing `_build_llm` forwarding
  working unchanged.
- LLM gateway integration story (#197). The cluster's
  `engine_secrets` payload is engine-agnostic; whatever keys the
  cluster populates flow through to the adapter's `secrets_in_env`
  bridge unchanged. We don't need a new cluster branch for openhands
  because the env-key shape is what claude-code already uses
  (`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` and the
  per-provider analogues we listed in
  `_OPENHANDS_SDK_ENV_KEYS` back in Phase 0).

## Action

- `packages/cluster/doorae/engines/catalog.py`:
  - openhands `EngineCatalogEntry` grew from 3 → 14 models.
  - Anthropic group (5): mirrors claude-code's claude-{opus-4-7,
    opus-4-6, sonnet-4-6, sonnet-4-5, haiku-4-5}, all narrowed to
    `low / medium / high / xhigh / max` (claude-code's effort set).
  - OpenAI group (5): mirrors codex's gpt-{5.5, 5.4, 5.4-mini,
    5.3-codex, 5.2}. gpt-5.5 carries the same backend-side caveat
    codex's entry documents (announcement-only, no round-trip
    verification yet). Per-model effort lists match codex —
    gpt-5.4-mini drops `xhigh`, gpt-5.3-codex drops `minimal`,
    gpt-5.2 narrows to `low / medium / high`.
  - Google group (4): mirrors gemini-cli's gemini-{3-pro-preview,
    3-flash-preview, 2.5-pro, 2.5-flash}, all narrowed to
    `low / medium / high` (the gemini-cli adapter's translated
    effort set).
- `packages/agent/docs/engines.md`: Phase 4 catalog listing
  refreshed (3 → 14) and a new "LLM Gateway (#197) 통합" section
  spells out the gateway story — no openhands-specific cluster code
  needed because `engine_secrets` is engine-agnostic, so the gateway
  populating provider-specific BASE_URL/AUTH_TOKEN keys flows to the
  adapter's `secrets_in_env` bridge unchanged.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` Phase 4
asked for "multi-provider 카탈로그 확장 + LLM gateway 통합". Three
shape options for the catalog were on the table:

- **Provider-prefixed model ids** (chosen). litellm-style
  `anthropic/claude-opus-4-7`. Matches the SDK's routing
  expectation, no separate provider field, no extra translation
  layer. The existing `_build_llm` already forwards model strings
  unchanged, so this is the lowest-friction shape.
- **Separate `provider` field on `EngineModel`**. Cleaner data
  model in isolation, but doubles the indexing surface (admin UI
  filters, API clients). Rejected because the routing was already
  resolved by the prefix in Phase 0 — keeping it that way avoids
  schema migration pressure on `EngineModel`.
- **A flat global catalog where each provider model appears once
  and `engine` is just metadata**. Tempting if the long-term plan
  is to retire CLI engines entirely (which it is, after Phase 5
  validation), but it would force a bigger admin UI rework now
  to handle the cross-engine duplication. Rejected — Phase 6 is
  the right place to revisit the catalog shape after CLI engines
  are flagged as legacy.

What tipped the scale: Phase 4 is a stepping stone before the
Phase 5 validation. The validator wants to compare openhands to
claude-code / codex / gemini-cli on the same task with the same
model. That comparison only works if the operator can actually
select equivalent models on both sides. Mirroring the existing
catalogs makes the comparison trivial; inventing a new catalog
shape would force the validator to also normalise model IDs.

What I explicitly didn't expand on:

- New providers (Mistral, Cohere, etc.). litellm supports many
  more, but Phase 4's job is parity with the existing CLI engines'
  coverage. New providers come in subsequent work once the
  validation has signed off the existing three.
- LLM gateway code in cluster. The gateway already publishes the
  required env vars for claude-code; openhands consumes the same
  env-var contract. Documenting that explicitly in `engines.md`
  closes the loop without forcing a cluster code change that has
  no per-engine logic to add.
- Per-model price / context-window metadata. Useful eventually
  for FinOps surfaces but not part of Phase 4's scope; the
  existing `EngineModel` dataclass deliberately stays minimal.

Assumptions worth flagging if they break later:
- `provider/model` is the litellm canonical routing prefix. If
  the SDK switches to a richer routing API (e.g. an explicit
  `provider` parameter on `LLM`), the catalog shape becomes
  redundant — the fix is mechanical (split prefix → new field)
  but happens in the adapter and catalog together.
- Each per-model `reasoning_levels` matches the underlying
  provider's actual acceptance set. The narrowing here mirrors the
  CLI engines' catalog entries verbatim; if a provider expands
  its taxonomy (Anthropic adds a 6th step, etc.) both catalogs
  need to be updated in lockstep. The drift would surface as a
  rejected runtime validation rather than a silent failure, which
  is the right failure mode.
- gpt-5.5's announcement-only status. codex's catalog notes
  ChatGPT-account auth may reject it at runtime; openhands runs
  via litellm against the OpenAI API directly, where the same
  caveat applies until production verification.

## Result

Phase 4 complete. The openhands engine now offers 14 models
spanning Anthropic / OpenAI / Google, matching what each
dedicated CLI engine advertises. Per-model `reasoning_levels`
narrowing is verified end-to-end via the catalog validators —
`is_valid_reasoning_effort` correctly accepts each provider's
full set and rejects the others (Anthropic 'high' yes, 'minimal'
no; OpenAI 'minimal' yes; Google 'xhigh' no).

Coverage: 11 / 11 cluster `engine_catalog` tests pass.

LLM gateway story documented but no cluster code change needed —
the adapter's `_OPENHANDS_SDK_ENV_KEYS` already covers the
gateway's per-provider env-var shape.

Still pending: Phase 5 (validation scenarios that now actually
have a comparable model surface to test against), Phase 6 (CLI
engine deprecation marking).
