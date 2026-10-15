"""Machine configuration: ~/.anygarden/machine.toml + .token file."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from anygarden.machine.safefs import secure_chmod

ANYGARDEN_DIR = Path.home() / ".anygarden"
CONFIG_PATH = ANYGARDEN_DIR / "machine.toml"
TOKEN_PATH = ANYGARDEN_DIR / "machine.token"
DATA_DIR_ENV = "ANYGARDEN_MACHINE_DATA_DIR"


@dataclass(frozen=True)
class MachinePaths:
    """Where one machine daemon keeps its state (#848).

    The default layout is the historical one under ``~/.anygarden`` so an
    upgraded remote machine still re-adopts the agents it already runs. A
    data directory holds everything one daemon owns, so a second daemon on
    the same computer (for example next to an integrated node) never shares
    agent runtime records or the workspace signing key with the first.
    Engine installs and engine logins stay shared.
    """

    config: Path
    token: Path
    agents_root: Path
    # ``None`` keeps the daemon's historical default for these two files.
    workspace_registry: Path | None = None
    signing_key: Path | None = None
    data_dir: Path | None = None

    @classmethod
    def for_data_dir(cls, data_dir: Path | str | None) -> MachinePaths:
        if data_dir is None or str(data_dir) == "":
            # Read the module globals at call time: tests redirect them.
            return cls(
                config=CONFIG_PATH,
                token=TOKEN_PATH,
                agents_root=ANYGARDEN_DIR / "agents",
            )
        root = Path(data_dir).expanduser().absolute()
        if root.is_symlink():
            raise ValueError(f"Machine data directory must not be a symlink: {root}")
        return cls(
            config=root / "machine.toml",
            token=root / "machine.token",
            agents_root=root / "agents",
            workspace_registry=root / "workspaces.json",
            signing_key=root / "workspace-signing.key",
            data_dir=root,
        )


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


def save_connection(
    config: MachineConfig, token: str, paths: MachinePaths | None = None
) -> None:
    """Save a web-issued identity without creating another server machine.

    Restore the previous token if saving the companion config fails. Each file
    is replaced atomically and is private from the moment it is created.
    """
    paths = paths or MachinePaths.for_data_dir(None)
    token_path = paths.token
    previous_token = token_path.read_text() if token_path.exists() else None
    save_token(token, path=token_path)
    try:
        config.save(paths.config)
    except Exception:
        if previous_token is None:
            token_path.unlink(missing_ok=True)
        else:
            _write_private(token_path, previous_token)
        raise
