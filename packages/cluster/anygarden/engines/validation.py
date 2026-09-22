"""Configuration checks shared by API and automatic lifecycle dispatch."""

import re

PROVIDER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
PI_PROVIDER_REQUIRED = "Pi requires a valid explicit provider; configure Provider in agent settings before starting."


def pi_provider_error(engine: str, provider: str | None) -> str | None:
    if engine == "pi-cli" and (
        not isinstance(provider, str)
        or re.fullmatch(PROVIDER_PATTERN, provider) is None
    ):
        return PI_PROVIDER_REQUIRED
    return None


REMOVED_ENGINES = frozenset(
    {"claude-code", "claude_code", "gemini-cli", "gemini_cli", "openhands"}
)
ENGINE_REMOVED_MESSAGE = "This engine has been removed. Create a codex-cli or pi-cli agent and transfer the settings you want to keep; existing configuration and history are preserved."


def removed_engine_error(engine: str) -> str | None:
    return ENGINE_REMOVED_MESSAGE if engine in REMOVED_ENGINES else None


PYTHON_RUNTIME_REQUIRED = "Codex and Pi require the Python agent runtime; set runtime=python in agent settings. Existing configuration and history are preserved."


def engine_runtime_error(engine: str, runtime: str | None) -> str | None:
    if engine in {"codex-cli", "pi-cli"} and runtime not in {None, "python"}:
        return PYTHON_RUNTIME_REQUIRED
    return None
