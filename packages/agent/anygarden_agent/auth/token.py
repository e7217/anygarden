"""Token loading from environment variable, CLI flag, or file."""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_TOKEN_FILE = Path.home() / ".anygarden" / "agent.token"


def load_token(
    *,
    cli_token: str | None = None,
    env_var: str = "ANYGARDEN_TOKEN",
    token_file: Path | None = None,
) -> str:
    """Resolve an authentication token using the following priority:

    1. Explicit ``cli_token`` (from ``--token`` flag)
    2. Environment variable (default ``ANYGARDEN_TOKEN``)
    3. Token file (default ``~/.anygarden/agent.token``)

    Raises ``RuntimeError`` if no token can be found.
    """
    # 1. CLI flag
    if cli_token:
        logger.debug("token.source", source="cli_flag")
        return cli_token

    # 2. Environment variable
    env_token = os.environ.get(env_var)
    if env_token:
        logger.debug("token.source", source="env_var")
        return env_token

    # 3. Token file
    path = token_file or _DEFAULT_TOKEN_FILE
    if path.is_file():
        # Warn if the token file is world-readable
        try:
            mode = path.stat().st_mode
            if mode & 0o077:
                logger.warning(
                    "token.insecure_permissions",
                    hint=f"chmod 600 {path}",
                )
        except OSError:
            pass
        token = path.read_text().strip()
        if token:
            logger.debug("token.source", source="file")
            return token

    raise RuntimeError(
        f"No token found. Provide --token, set {env_var}, "
        f"or place a token in {_DEFAULT_TOKEN_FILE}"
    )
