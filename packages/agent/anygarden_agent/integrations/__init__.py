"""Codex and Pi engine integrations with shared room execution helpers."""

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
    "pi-cli": "anygarden_agent.integrations.pi_cli",
    # #496 — codex-cli: ``codex exec`` subprocess engine, decoupled from the
    # codex-python SDK's bundled binary version (#506 removed the SDK codex).
    "codex-cli": "anygarden_agent.integrations.codex_cli",
}

# Engine name -> adapter class name
_ADAPTER_CLASSES: dict[str, str] = {
    "pi-cli": "PiCliAdapter",
    "codex-cli": "CodexCliAdapter",
}


def get_adapter(engine: str, **kwargs: Any) -> EngineAdapter:
    """Lazy-load and instantiate an engine adapter by name.

    Args:
        engine: Engine identifier (e.g. "codex-cli").
        **kwargs: Keyword arguments forwarded to the adapter constructor.

    Returns:
        An EngineAdapter instance (not yet started -- call ``await adapter.start()``).

    Raises:
        ValueError: If the engine name is not recognized.
    """
    error = removed_engine_error(engine)
    if error:
        raise ValueError(error)
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


REMOVED_ENGINES = frozenset(
    {"claude-code", "claude_code", "gemini-cli", "gemini_cli", "openhands"}
)
ENGINE_REMOVED_MESSAGE = "This engine has been removed. Create a codex-cli or pi-cli agent and transfer the settings you want to keep; existing configuration and history are preserved."


def removed_engine_error(engine: str) -> str | None:
    return ENGINE_REMOVED_MESSAGE if engine in REMOVED_ENGINES else None
