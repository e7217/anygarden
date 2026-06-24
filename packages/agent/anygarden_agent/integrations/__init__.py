"""Engine integrations -- adapters for Claude Code, Codex, Gemini CLI, and OpenHands.

Issue #355 — OpenHands V1 SDK is the in-process Python alternative to
the three CLI-subprocess adapters; see
``.tmp/plan-355-openhands-engine-migration.md`` for the phased
migration that ends with deprecation marking (CLI removal is tracked
separately).
"""

from __future__ import annotations

import importlib
from typing import Any

from anygarden_agent.integrations.base import EngineAdapter

__all__ = [
    "EngineAdapter",
    "ENGINES",
    "get_adapter",
]

# Lazy-load mapping: engine name -> module path
ENGINES: dict[str, str] = {
    "claude-code": "anygarden_agent.integrations.claude_code",
    "codex": "anygarden_agent.integrations.codex",
    # #496 — codex-cli: ``codex exec`` subprocess engine, decoupled from
    # the codex-python SDK's bundled binary version.
    "codex-cli": "anygarden_agent.integrations.codex_cli",
    "gemini-cli": "anygarden_agent.integrations.gemini_cli",
    "openhands": "anygarden_agent.integrations.openhands_engine",
}

# Engine name -> adapter class name
_ADAPTER_CLASSES: dict[str, str] = {
    "claude-code": "ClaudeCodeAdapter",
    "codex": "CodexAdapter",
    "codex-cli": "CodexCliAdapter",
    "gemini-cli": "GeminiCliAdapter",
    "openhands": "OpenHandsAdapter",
}


def get_adapter(engine: str, **kwargs: Any) -> EngineAdapter:
    """Lazy-load and instantiate an engine adapter by name.

    Args:
        engine: Engine identifier (e.g. "claude-code").
        **kwargs: Keyword arguments forwarded to the adapter constructor.

    Returns:
        An EngineAdapter instance (not yet started -- call ``await adapter.start()``).

    Raises:
        ValueError: If the engine name is not recognized.
    """
    if engine not in ENGINES:
        raise ValueError(
            f"Unknown engine {engine!r}. "
            f"Available engines: {', '.join(sorted(ENGINES))}"
        )

    module_path = ENGINES[engine]
    class_name = _ADAPTER_CLASSES[engine]

    module = importlib.import_module(module_path)
    adapter_cls = getattr(module, class_name)
    return adapter_cls(**kwargs)
