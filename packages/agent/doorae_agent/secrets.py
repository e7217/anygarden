"""Private secrets storage for the doorae-agent process.

The machine daemon pipes per-agent ``engine_secrets`` (API keys and
similar credentials) to ``doorae-agent`` via stdin at startup. The
payload is a single JSON object of ``{env_var_name: value}`` — read
once, stored here, and stdin is closed.

Design goals (Issue #184 follow-up):

* ``doorae-agent``'s ``/proc/self/environ`` must stay clean. A
  compromised LLM tool call (Bash, Read) can always ``cat`` its own
  ``/proc/self/environ``; if we pass API keys in the initial env the
  LLM exfiltrates them for free.
* Secrets are kept only in this module's private dict. Callers that
  need to hand a key to an engine:

  - Subprocess CLI engines (Gemini): use :func:`env_with_secrets` to
    produce an env dict for ``create_subprocess_exec(env=...)``. The
    child inherits the keys, the parent's env stays clean.
  - In-process Python SDKs (Claude Code SDK, Codex SDK) that read
    credentials from ``os.environ``: use the :func:`secrets_in_env`
    context manager. It temporarily places the requested keys into
    ``os.environ`` for the SDK to discover during construction, then
    restores the original values on exit.

Known limit — MCP subprocess spawn during SDK construction
---------------------------------------------------------
``secrets_in_env`` cannot prevent an MCP subprocess spawned BY a
Python SDK **during** the context from inheriting the keys via env.
Closing that path requires the SDK itself to accept an explicit
``api_key=`` parameter and not touch ``os.environ`` at all. Tracked
as a follow-up on each adapter.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Iterator

# Module-private storage. Never exposed via ``os.environ``.
_secrets: dict[str, str] = {}


def load_from_stdin() -> None:
    """Read a single JSON object from stdin and store in private state.

    Called once at ``doorae-agent`` startup by the CLI entry point.
    The machine daemon writes the payload and closes stdin, so
    :func:`sys.stdin.read` returns the full buffer and then EOF.

    Guards:

    * ``isatty``-check short-circuits interactive dev runs where stdin
      is a terminal (otherwise the read would block).
    * Missing / empty / invalid stdin is treated as "no secrets". The
      engine then falls back to its own credential discovery (host env,
      gcloud ADC, etc.), matching pre-#184 behaviour.
    * Non-dict JSON is rejected to avoid pollution from arbitrary
      payloads.
    """
    global _secrets
    if sys.stdin is None or sys.stdin.isatty():
        return
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return
    if not raw.strip():
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    _secrets = {
        str(k): str(v)
        for k, v in data.items()
        if isinstance(k, str)
    }
    try:
        sys.stdin.close()
    except OSError:
        pass


def set_secrets(secrets: dict[str, str]) -> None:
    """Replace stored secrets. Intended for tests only."""
    global _secrets
    _secrets = dict(secrets)


def clear() -> None:
    """Drop stored secrets. Intended for tests only."""
    global _secrets
    _secrets = {}


def get(key: str) -> str | None:
    """Return the stored value for *key* or ``None``."""
    return _secrets.get(key)


def all_secrets() -> dict[str, str]:
    """Return a copy of the stored secrets dict."""
    return dict(_secrets)


def env_with_secrets(
    base_env: dict[str, str] | None = None,
    keys: list[str] | None = None,
) -> dict[str, str]:
    """Return a copy of *base_env* (or ``os.environ``) with stored
    secrets merged in.

    If *keys* is given, only those keys are merged. Otherwise all
    stored secrets are merged. Use for subprocess spawns so the child
    process sees the API keys while the parent ``doorae-agent`` stays
    clean.
    """
    env = dict(base_env if base_env is not None else os.environ)
    if keys is None:
        env.update(_secrets)
    else:
        for k in keys:
            if k in _secrets:
                env[k] = _secrets[k]
    return env


@contextlib.contextmanager
def secrets_in_env(keys: list[str]) -> Iterator[None]:
    """Temporarily place *keys* into ``os.environ`` for in-process SDKs.

    Use as a ``with`` block around SDK client construction when the
    SDK reads credentials from the environment. Keys absent from the
    stored secrets are ignored. On exit, the previous ``os.environ``
    state for each key is restored (existing value preserved, or the
    key removed if it wasn't set before).
    """
    prior: dict[str, str | None] = {}
    for k in keys:
        prior[k] = os.environ.get(k)
        value = _secrets.get(k)
        if value is not None:
            os.environ[k] = value
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
