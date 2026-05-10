# fix(gateway): rewrite ollama/ -> ollama_chat/ to avoid format=json clamp

- Commit: `ea32a98` (ea32a98...)
- Author: Changyong Um
- Date: 2026-05-10T15:46:00+09:00
- PR: —

## Situation

Once OpenHands runtime tools (Terminal/FileEditor/TaskTracker) were
registered, ``oh-agent04`` (qwen3.6:27b) successfully ran shell
commands but its second-turn summary arrived as a JSON blob like

    {"tool_code": "default", "tool_name": "default",
     "tool_output": "<actual answer>"}

instead of plain markdown prose. Direct probes isolated the layer:

- ``ollama /api/chat`` (native) with the same tool sequence: clean
  markdown response.
- ``ollama /v1/chat/completions`` (OpenAI-compat): empty response
  (qwen's compat layer is immature).
- LiteLLM (``ollama/qwen3.6:27b``) → ollama: JSON envelope.

Reproducing through the LiteLLM Python SDK with verbose logging
revealed the cause:

    Final returned optional params: {'format': 'json',
                                     'functions_unsupported_model': [...]}

LiteLLM ships two Ollama providers. ``ollama/`` is the legacy
``/api/generate`` path that tags the model as
``functions_unsupported_model`` and clamps the upstream call to
``format: 'json'``. The model can no longer emit free prose — it
must return a single JSON object. Tool-capable models then pick the
closest JSON shape from training and wrap their answer in a fake
Gemini-style envelope. ``ollama_chat/`` uses native ``/api/chat``
with OpenAI ``tool_calls`` passthrough — no JSON clamp, no prompt
rewriting.

## Task

- Make every Ollama row in ``llm_gateway_models`` reach LiteLLM as
  ``ollama_chat/<model>``, regardless of how it was historically
  stored.
- Stop creating new ``ollama/`` rows from the admin UI.
- Preserve idempotency (admins typing the canonical form get no
  double-prefix) and avoid hijacking unrelated providers
  (``"openai/some-ollama-tuned"`` must not be touched).
- Lock the contract with regression tests.

## Action

- ``packages/cluster/doorae/llm_gateway/config_writer.py`` —
  added ``_rewrite_ollama_provider(upstream)`` that maps
  ``ollama/<rest>`` → ``ollama_chat/<rest>`` and passes everything
  else through unchanged. ``render_config`` now applies it to
  ``m.upstream_model`` before emitting the litellm_params dict. The
  helper has a long docstring explaining the JSON-envelope failure
  mode so future refactors don't strip the rewrite.
- ``packages/cluster/tests/test_llm_gateway_config_writer.py`` —
  three new cases:
  - ``test_ollama_provider_rewritten_to_ollama_chat``: ``ollama/x``
    → ``ollama_chat/x`` in the rendered yaml.
  - ``test_ollama_chat_already_canonical_passes_through``: idempotency.
  - ``test_non_ollama_provider_unaffected``: anthropic/openai untouched.
  Existing ``test_ollama_model_with_api_base_extra_param`` updated
  to assert the rewritten form, with a comment pointing at the
  rationale.
- ``packages/cluster/tests/test_llm_gateway_bootstrap.py`` —
  updated the ``test_rendered_yaml_written_to_config_path`` assertion
  to expect the canonical ``ollama_chat/qwen3-coder:30b`` substring.
- ``packages/cluster/frontend/src/components/admin-llm-gateway/ModelDialog.tsx`` —
  changed the Ollama provider's ``upstreamPrefix`` from
  ``ollama/`` to ``ollama_chat/`` so newly created rows store the
  canonical form. A comment block explains the JSON-envelope
  rationale and notes that the writer rewrites old rows
  defensively.

(In addition, the live DB row was updated in-place from
``ollama/qwen3.6:27b`` to ``ollama_chat/qwen3.6:27b`` and the running
LiteLLM was bounced — those are operational steps, not part of the
diff.)

## Decisions

- **Rewrite at render time vs. one-time DB migration**: render-time
  is invisible to the admin UI (DB stays as-typed) and cannot drift
  from the live yaml. A one-shot migration would also fix existing
  rows but offers no protection against a future admin who pastes
  ``ollama/`` (e.g. from upstream LiteLLM docs). Rewriting on every
  render is idempotent and self-healing, so it's the durable safety
  net. We additionally normalise the *frontend* prefix and the live
  DB row so the canonical form is the visible reality going forward.
- **Single rewrite point in ``config_writer`` rather than at the
  admin POST endpoint**: the API layer already accepts arbitrary
  ``upstream_model`` strings (custom providers exist). Restricting
  there would force an allowlist that doesn't match the freedom the
  field is designed to have. The rewrite belongs at the integration
  boundary with LiteLLM.
- **No automatic rewrite for ``vllm/`` or other "OpenAI-compatible"
  prefixes** — the JSON-format clamp is specific to LiteLLM's
  ``ollama/`` legacy provider. Extending the rule would risk hijacking
  legitimate prefixes. If another provider needs the same treatment,
  add a separate explicit clause.
- **Trigger to revisit**: if LiteLLM ever unifies its two Ollama
  providers, or starts emitting native tool_calls from ``ollama/``,
  the rewrite becomes vestigial — keep it idempotent so it stays
  cheap, but the lock-test ``test_ollama_chat_already_canonical_passes_through``
  ensures it remains safe.

## Result

- Live verification: posting the same tool sequence (user → assistant
  with tool_calls → tool result) through the gateway after the
  rewrite returns clean markdown ("### 📊 메모리 현황 …") instead of
  the ``{"tool_code": "default", "tool_output": ...}`` blob.
- Full ``packages/cluster`` suite: 956 passed (3 new gateway tests
  added, existing assertions updated).
- DB row and frontend dropdown both store ``ollama_chat/`` going
  forward; legacy rows still render correctly via the rewrite.
