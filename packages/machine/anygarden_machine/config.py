"""Machine configuration: ~/.anygarden/machine.toml + .token file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from anygarden_machine.safefs import secure_chmod

ANYGARDEN_DIR = Path.home() / ".anygarden"
CONFIG_PATH = ANYGARDEN_DIR / "machine.toml"
TOKEN_PATH = ANYGARDEN_DIR / "machine.token"


class MachineConfig(BaseSettings):
    """Machine daemon configuration loaded from file, env, or defaults."""

    model_config = {"env_prefix": "ANYGARDEN_MACHINE_", "extra": "ignore"}

    machine_id: str = ""
    name: str = ""
    server_url: str = "wss://localhost:8000/ws/machine"
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
            # JSON basic-string escapes are valid TOML too. Keep Unicode intact
            # (JSON surrogate pairs are not TOML escapes) and escape DEL as well.
            return json.dumps(s, ensure_ascii=False)[1:-1].replace("\x7f", "\\u007f")

        lines = [
            f'machine_id = "{_toml_escape(self.machine_id)}"',
            f'name = "{_toml_escape(self.name)}"',
            f'server_url = "{_toml_escape(self.server_url)}"',
        ]
        if self.labels:
            # Serialize labels as a proper TOML inline table
            label_parts = [
                f'"{_toml_escape(k)}" = "{_toml_escape(str(v))}"'
                for k, v in self.labels.items()
            ]
            lines.append(f"labels = {{ {', '.join(label_parts)} }}")
        _write_private(config_path, "\n".join(lines) + "\n")


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
    _write_private(token_path, token + "\n")


def _write_private(path: Path, value: str) -> None:
    """Create private contents before replacement; never truncate a token symlink."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        secure_chmod(temp_path, 0o600)
        with os.fdopen(fd, "w") as handle:
            fd = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if fd != -1:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def save_connection(config: MachineConfig, token: str) -> None:
    """Save a web-issued identity without creating another server machine.

    Restore the previous token if saving the companion config fails. Each file
    is replaced atomically and is private from the moment it is created.
    """
    previous_token = TOKEN_PATH.read_text() if TOKEN_PATH.exists() else None
    save_token(token)
    try:
        config.save()
    except Exception:
        if previous_token is None:
            TOKEN_PATH.unlink(missing_ok=True)
        else:
            _write_private(TOKEN_PATH, previous_token)
        raise
