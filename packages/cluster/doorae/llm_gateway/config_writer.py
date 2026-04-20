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
from typing import Iterable

from doorae.db.models import LLMGatewayModel


def render_config(
    models: Iterable[LLMGatewayModel],
    *,
    master_key_env: str = "DOORAE_LITELLM_MASTER_KEY",
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
    raise NotImplementedError  # Phase 2 — TDD implementation


def config_hash(rendered_yaml: str) -> str:
    """Stable hash of the yaml body for the Status panel.

    Used to answer "is the running process loading the same config
    the DB would produce now?" without reading the disk file. Callers
    compare ``config_hash(render_config(db_state))`` against the
    supervisor's recorded hash from the last successful spawn.
    """
    return hashlib.sha256(rendered_yaml.encode("utf-8")).hexdigest()[:16]
