# feat(agents,llm-gateway,ui): add codex-extra virtual engine for LiteLLM-routed agents

- Commit: `554fd43` (554fd43ab44db44e3f88a858586c3e5186b818bc)
- Author: Changyong Um
- Date: 2026-04-25T00:16:11+09:00
- PR: —

## Situation

The embedded LiteLLM gateway (issue #197) quietly redirected every
``codex`` and ``claude-code`` agent through the gateway whenever the
``DOORAE_LLM_GATEWAY_ENABLED`` flag was on. This was invisible in the
UI: the Add Agent dialog's Model dropdown only ever showed the static
catalog from ``engines/catalog.py`` and had no affordance to pick (or
even see) the models an admin registered on the LLM Gateway page.

The practical failure mode: an admin turning the flag on with an empty
or mismatched ``llm_gateway_models`` table ended up with agents that
spawned fine but 404'd at LiteLLM on every request, with no obvious
link back to the UI surface that would let them fix it. In this repo's
case the live gateway had one ``ollama/qwen3.6:27b`` row registered
while the running agents were asking for ``claude-opus-4-7`` and
``gpt-5.4``.

## Task

- Surface LLM Gateway models in the Add Agent dialog so admins can
  actually pick them.
- Make gateway routing an explicit, per-agent decision rather than a
  side-effect of a server flag, so flipping the flag on doesn't
  silently reroute already-working agents.
- Keep the machine daemon and spawner unaware of the new construct —
  they only know about real CLI binaries on disk.
- Limit scope to Codex. ``claude-code-extra`` was considered and
  dropped because LiteLLM's Anthropic-format round-trip with
  non-Anthropic upstreams isn't a pairing we want to surface yet.

## Action

- `packages/cluster/doorae/engines/catalog.py` — added a ``codex-extra``
  ``EngineCatalogEntry`` with empty ``models`` / ``default_model`` (the
  list is populated at request time from ``llm_gateway_models``), plus
  a ``VIRTUAL_ENGINE_TO_BASE`` map and ``base_engine()`` /
  ``is_gateway_engine()`` helpers. Re-exported from
  ``engines/__init__.py``.
- `packages/cluster/doorae/api/v1/agents.py` — ``get_engine_models``
  now depends on the DB, merges gateway rows for virtual engines, and
  picks the first gateway row as the default when the static catalog
  has none. ``EngineModelOut`` grows a ``source`` marker
  (``"builtin"`` / ``"gateway"``).
- `packages/cluster/doorae/api/v1/machines.py` —
  ``list_machine_engines`` augments its response with ``codex-extra``
  when the ``llm_gateway_supervisor`` is in ``running`` state and the
  machine reports a real ``codex`` install. Hidden when the supervisor
  is FAILED/STARTING so admins don't pick a mode that would 503.
- `packages/cluster/doorae/scheduler/lifecycle.py` —
  ``_build_gateway_engine_secrets`` reversed from auto-enrollment to
  opt-in: only ``codex-extra`` returns gateway env; plain ``codex`` /
  ``claude-code`` / etc. return ``{}``. ``select_machine_for``, the
  ``sync_desired_state`` frame's ``engine`` field, and the MCP template
  merge path all collapse virtual → base via ``base_engine()`` so the
  machine side continues seeing ``codex``.
- `packages/cluster/frontend/src/components/AdminMachines.tsx` — added
  ``"Codex (extra)"`` to ``ENGINE_LABELS``; the Model dropdown now
  splits entries into ``<optgroup>`` sections (Built-in / LLM Gateway)
  when gateway rows are present, with a hint string when
  ``codex-extra`` is selected but no gateway models are registered.
- `packages/cluster/frontend/src/hooks/useAgents.ts` — ``EngineModel``
  gets an optional ``source`` field (``'builtin' | 'gateway'``).
- `packages/cluster/tests/test_engine_catalog.py` — the
  ``default_model in models`` invariant skips virtual engines (both
  fields are intentionally empty).
- `packages/cluster/tests/test_llm_gateway_manifest_injection.py` —
  rewritten for the opt-in contract: plain ``codex`` / ``claude-code``
  now assert ``== {}`` even with the flag on, and ``codex-extra``
  asserts the ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` pair.

## Decisions

- **Virtual engine vs. per-agent boolean flag.** An earlier sketch
  added a ``via_gateway: bool`` column on ``agents``. Rejected because
  (a) it required a migration, (b) it split "how do we reach the LLM"
  across two fields where previously one string carried the full
  answer, and (c) it made the UI show a checkbox that only meant
  something for two engines. A dedicated ``codex-extra`` id encodes the
  choice in the same string the rest of the stack already keys on.
- **Revert auto-enrollment (breaking).** The alternative was to keep
  auto-enrollment and only *add* ``codex-extra`` as a shortcut to
  surface gateway models. Rejected because the flag-driven behavior was
  producing the current broken state: live agents were routed through a
  gateway whose config didn't list their models, with no UI affordance
  to diagnose. Making routing explicit trades a one-time migration
  bump (three already-running agents shift back to host auth on the
  next sync) for a mental model the admin can actually navigate.
- **Cluster-side engine translation.** Putting ``base_engine()`` at the
  scheduler boundary (before sending the ``sync_desired_state`` frame
  and before querying ``MachineEngine``) lets the machine daemon stay
  untouched. The alternative — teaching the daemon and spawner about
  virtual engines — would have bled a UI concept into every
  installation's update path, including older daemons that would
  silently break on unknown engine strings.
- **Skip ``claude-code-extra``.** Confirmed with the user mid-thread.
  LiteLLM's Anthropic-format serving over non-Anthropic upstreams has
  rough edges for tool-calling, and the only gateway entry the repo
  currently exercises (ollama) isn't Anthropic-compatible. Adding the
  pair symmetrically would have shipped a trap rather than a feature.
- **Augmentation only when gateway is RUNNING.** Decided to hide
  ``codex-extra`` from ``list_machine_engines`` when the supervisor is
  FAILED/STARTING rather than showing it disabled. A disabled entry
  would still let someone save a draft that would 503 at spawn. Hiding
  it pushes the admin to the LLM Gateway status panel first, which is
  the only place the underlying problem is diagnosable.
- Assumption to revisit: if LiteLLM's Anthropic route gets mature
  cross-provider support, a symmetric ``claude-code-extra`` becomes
  attractive — at which point ``VIRTUAL_ENGINE_TO_BASE`` grows a
  second entry and both wiring points (``_build_gateway_engine_secrets``
  and the machine-augment gate) need mirror additions.

## Result

- New ``codex-extra`` engine is selectable in the Add Agent dialog on
  machines with Codex installed while the gateway is RUNNING. Its
  Model dropdown shows gateway-registered rows (today:
  ``qwen3.6:27b · ollama``) under an "LLM Gateway" group, distinct
  from the static Built-in entries.
- Plain ``codex`` and ``claude-code`` agents no longer auto-route
  through the gateway — they use host credentials
  (``~/.codex/auth.json``, ``ANTHROPIC_API_KEY``, etc.) as intended
  pre-#197. The three already-running agents on this deployment will
  drop back to host auth on their next sync.
- Unit tests: full cluster suite (734 tests) green; agent gateway-env
  injection tests green; frontend ``npm run build`` (tsc + vite) clean.
- Live API smoke against ``127.0.0.1:8001`` was not completed — the
  dev server was mid-reload at the end of the session. Follow-up is
  simply to hit ``GET /api/v1/machines/{id}/engines`` and
  ``GET /api/v1/agents/engines/codex-extra/models`` to confirm the
  response shapes in a live instance.
- Not addressed (explicit non-goals): no Gemini/OpenHands/DeepAgents
  gateway wiring; no migration script to rename existing ``codex``
  rows to ``codex-extra`` (admin opts in per new agent).
