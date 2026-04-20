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
            "model": m.upstream_model,
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
