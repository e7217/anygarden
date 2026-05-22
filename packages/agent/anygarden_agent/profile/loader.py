"""Load agent profiles from ``~/.anygarden/agents/*.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog
import yaml

from anygarden_agent.profile.schema import AgentProfile

logger = structlog.get_logger(__name__)

_DEFAULT_AGENTS_DIR = Path.home() / ".anygarden" / "agents"


def load_profile(
    name: str,
    agents_dir: Path | None = None,
) -> AgentProfile:
    """Load a single agent profile by name.

    Looks for ``<agents_dir>/<name>.yaml``.
    """
    directory = agents_dir or _DEFAULT_AGENTS_DIR
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Agent profile not found: {path}")

    data = yaml.safe_load(path.read_text())
    return AgentProfile.model_validate(data)


def list_profiles(
    agents_dir: Path | None = None,
) -> list[AgentProfile]:
    """List all agent profiles in the agents directory."""
    directory = agents_dir or _DEFAULT_AGENTS_DIR
    if not directory.is_dir():
        return []

    profiles: list[AgentProfile] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            profiles.append(AgentProfile.model_validate(data))
        except Exception as exc:
            logger.warning("profile.load_error", path=str(path), error=str(exc))
    return profiles
