"""Machine configuration: ~/.doorae/machine.toml + .token file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

DOORAE_DIR = Path.home() / ".doorae"
CONFIG_PATH = DOORAE_DIR / "machine.toml"
TOKEN_PATH = DOORAE_DIR / "machine.token"


class MachineConfig(BaseSettings):
    """Machine daemon configuration loaded from file, env, or defaults."""

    model_config = {"env_prefix": "DOORAE_MACHINE_"}

    machine_id: str = ""
    name: str = ""
    server_url: str = "wss://localhost:8000/ws/machine"
    max_agents: int = 4
    labels: dict = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> MachineConfig:
        """Load config from TOML file, falling back to env vars and defaults."""
        config_path = path or CONFIG_PATH
        overrides: dict = {}
        if config_path.exists():
            import tomllib

            with open(config_path, "rb") as f:
                overrides = tomllib.load(f)
        return cls(**overrides)

    def save(self, path: Path | None = None) -> None:
        """Save current config to TOML file."""
        config_path = path or CONFIG_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)

        def _toml_escape(s: str) -> str:
            """Escape a string value for safe TOML embedding."""
            return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

        lines = [
            f'machine_id = "{_toml_escape(self.machine_id)}"',
            f'name = "{_toml_escape(self.name)}"',
            f'server_url = "{_toml_escape(self.server_url)}"',
            f"max_agents = {self.max_agents}",
        ]
        if self.labels:
            # Serialize labels as a proper TOML inline table
            label_parts = [
                f'"{_toml_escape(k)}" = "{_toml_escape(str(v))}"'
                for k, v in self.labels.items()
            ]
            lines.append(f"labels = {{ {', '.join(label_parts)} }}")
        config_path.write_text("\n".join(lines) + "\n")


def load_token(path: Path | None = None) -> str:
    """Load machine token from file. Returns empty string if not found."""
    token_path = path or TOKEN_PATH
    if not token_path.exists():
        return ""
    return token_path.read_text().strip()


def save_token(token: str, path: Path | None = None) -> None:
    """Save machine token to file with chmod 600."""
    token_path = path or TOKEN_PATH
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token + "\n")
    os.chmod(token_path, 0o600)
