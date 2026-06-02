"""Shared litellm-binary presence check + install hint (#406).

Single source of truth for "is the ``litellm`` proxy CLI reachable?" and
the message we show when it is not. Imported by both ``anygarden init``
(pre-flight warning) and the gateway supervisor (spawn-failure detail) so
the two surfaces never drift apart.

Stdlib-only on purpose: the supervisor imports this module, so adding a
heavier dependency here would risk an import cycle / startup cost. Keep it
``os`` + ``shutil``.
"""

from __future__ import annotations

import os
import shutil

# Shown verbatim by ``anygarden init`` and embedded in the supervisor's
# ``last_error`` when the binary is missing. ``uv tool install`` is the
# project-standard install path (the cluster venv can't carry
# ``litellm[proxy]`` itself — its proxy extras pin a fastapi version that
# conflicts with cluster's pin; see config.py / #364).
INSTALL_HINT = (
    "litellm proxy CLI not found. To use the LLM Gateway, install it:\n"
    "    uv tool install 'litellm[proxy]'\n"
    "To point at a litellm installed elsewhere, set "
    "ANYGARDEN_LLM_GATEWAY_BINARY=/abs/path/to/litellm"
)


def resolve_litellm_binary() -> str:
    """Binary name/path the gateway will spawn and ``init`` should probe.

    Honours the ``ANYGARDEN_LLM_GATEWAY_BINARY`` override so ``init``'s
    pre-flight check agrees with what the supervisor actually spawns
    (config.py:llm_gateway_binary defaults to the same ``"litellm"``).
    Read straight from the environment rather than loading
    ``AnygardenSettings`` so ``init`` stays a cheap, side-effect-free
    file generator.
    """
    return os.environ.get("ANYGARDEN_LLM_GATEWAY_BINARY") or "litellm"


def litellm_available(binary: str | None = None) -> bool:
    """True when ``binary`` resolves to an executable on PATH (or abs path).

    ``shutil.which`` handles both a bare name (PATH lookup) and an absolute
    path (executable-bit check), so an override pointing at
    ``$HOME/.local/bin/litellm`` validates correctly.
    """
    return shutil.which(binary or resolve_litellm_binary()) is not None
