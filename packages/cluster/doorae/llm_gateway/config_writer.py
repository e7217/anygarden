"""DB → ``litellm.yaml`` renderer (#197).

Produces the yaml text the ``litellm`` subprocess consumes via
``--config``. Pure function over a list of model rows — no I/O, so
snapshot tests cover it without touching the filesystem.

Key invariant: **secrets never appear in the rendered output**. Every
credential is emitted as ``os.environ/DOORAE_LITELLM_<env_var_name>``
and materialised by the supervisor at spawn time. A reviewer can
read the yaml without seeing any live keys.

See ``docs/design/12-llm-gateway.md`` §12.3.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

import yaml

from doorae.db.models import LLMGatewayModel

# Prefix for env var names injected into the LiteLLM subprocess.
# Prefixing with ``DOORAE_LITELLM_`` isolates our keys from the
# server's own env so a stray ``ANTHROPIC_API_KEY`` in the operator's
# shell can't silently shadow what the admin configured through the UI.
_ENV_PREFIX = "DOORAE_LITELLM_"


def _rewrite_ollama_provider(upstream: str) -> str:
    """Rewrite ``ollama/<rest>`` → ``ollama_chat/<rest>`` transparently.

    Why: LiteLLM ships two Ollama providers and they behave very
    differently when an OpenAI-style ``tools=[...]`` array reaches
    the proxy:

    - ``ollama/`` is the legacy ``/api/generate`` provider. It tags
      the model as "functions_unsupported_model" and forces
      ``format: 'json'`` on the upstream request. The model then
      MUST respond with a single JSON object — it cannot emit free
      prose. Tool-using agents (OpenHands, Claude Code over a local
      LLM gateway, …) hit a turn where the model has already
      consumed the tool result and just needs to summarise it; with
      ``format: json`` clamped on, the model picks the closest
      JSON-shaped pattern from its training data and wraps the
      summary in something like
      ``{"tool_code": "default", "tool_name": "default",
      "tool_output": "<actual answer>"}``. The user sees a JSON
      blob instead of the answer.

    - ``ollama_chat/`` uses ``/api/chat`` with native ``tool_calls``
      passthrough — no JSON-format clamp, no prompt rewriting. This
      is the right provider for any modern Ollama install (0.4+
      ships native tool support). It has been the recommended
      choice in LiteLLM docs for tool-calling workloads since
      2024-11.

    Operationally: there is no scenario in doorae where ``ollama/``
    is preferable. The admin UI / DB stores whatever the operator
    types, and historically that has been ``ollama/<id>`` because
    the LiteLLM model catalog lists that name first. Rewriting at
    render time fixes existing rows and is invisible to the admin
    UI (the DB ``upstream_model`` is unchanged — only the rendered
    yaml is corrected).

    Idempotent: ``ollama_chat/<rest>`` passes through unchanged, so
    a future admin who explicitly types the canonical form is not
    double-rewritten.
    """
    if upstream.startswith("ollama/"):
        return "ollama_chat/" + upstream[len("ollama/") :]
    return upstream


def render_config(
    models: Iterable[LLMGatewayModel],
    *,
    master_key_env: str = f"{_ENV_PREFIX}MASTER_KEY",
) -> str:
    """Serialise enabled ``models`` into a LiteLLM proxy config yaml.

    Args:
        models: all ``LLMGatewayModel`` rows; the writer filters by
            ``enabled=True`` internally so callers don't have to.
        master_key_env: env var name the subprocess should read its
            master key from. The supervisor sets
            ``DOORAE_LITELLM_MASTER_KEY=<ephemeral>`` in the child's
            env right before spawn.

    Returns:
        A yaml string. An empty ``model_list: []`` is valid — LiteLLM
        boots and responds "model not found" to requests until an
        admin adds the first row (see §12.5).
    """
    model_list: list[dict[str, Any]] = []
    for m in models:
        if not m.enabled:
            continue
        params: dict[str, Any] = {
            # Rewrite legacy ``ollama/`` → ``ollama_chat/`` so tool-using
            # agents don't get clamped to ``format: json`` on the upstream
            # call. See ``_rewrite_ollama_provider`` for the full
            # rationale. Non-ollama models pass through unchanged.
            "model": _rewrite_ollama_provider(m.upstream_model),
            "api_key": f"os.environ/{_ENV_PREFIX}{m.api_key_ref}",
        }
        # Extras (temperature, max_tokens, custom headers, …) merge
        # *under* the core fields so a misconfigured row can't silently
        # override ``model`` / ``api_key``.
        if m.extra_params:
            for k, v in m.extra_params.items():
                if k not in params:
                    params[k] = v
        model_list.append({"model_name": m.model_name, "litellm_params": params})

    body = {
        "model_list": model_list,
        "general_settings": {
            "master_key": f"os.environ/{master_key_env}",
            # Stateless posture — LiteLLM's spend-tracking tables need
            # Postgres via Prisma. doorae logs usage itself in its
            # own SQLite (``llm_gateway_usage``) so we turn these off.
            "disable_spend_logs": True,
        },
    }
    return yaml.safe_dump(body, sort_keys=False)


def config_hash(rendered_yaml: str) -> str:
    """Stable hash of the yaml body for the Status panel.

    Used to answer "is the running process loading the same config
    the DB would produce now?" without reading the disk file. Callers
    compare ``config_hash(render_config(db_state))`` against the
    supervisor's recorded hash from the last successful spawn.
    """
    return hashlib.sha256(rendered_yaml.encode("utf-8")).hexdigest()[:16]
