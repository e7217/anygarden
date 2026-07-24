"""Engine CLI update execution (#553).

Distinct from :mod:`anygarden_machine.updater`, which reinstalls the daemon
itself (venv-pip, fixed ``anygarden-machine``). This module updates an *engine*
CLI on the machine.

Security (#550 lineage): the server sends only an engine **key**. This module
resolves the key to a package name via the registry allowlist
(:func:`~anygarden_machine.engines.registry.get_lifecycle`) — an unknown key is
refused before any subprocess runs — and executes the channel's argv without a
shell. Nothing from the server becomes a package name or shell fragment.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from anygarden_machine.engines.registry import get_lifecycle

log = structlog.get_logger()

# npm/pip installs can pull a dependency closure; give room but never hang.
UPDATE_TIMEOUT = 300  # seconds

# Injectable subprocess runner (for testing).
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass
class EngineUpdateResult:
    """Outcome of an engine update (never raised — always returned)."""

    ok: bool
    engine: str
    error: str | None


def run_engine_update(
    engine: str,
    *,
    python: str | None = None,
    runner: Runner = subprocess.run,
) -> EngineUpdateResult:
    """Update the ``engine`` CLI to its latest version.

    ``python`` is the interpreter for pip-channel engines (the venv that hosts
    the SDK) and is required for them — there is no fallback. It is ignored by
    npm-channel engines. ``runner`` is injectable for tests.

    Expected failures (unknown engine, non-zero exit, subprocess error) are
    captured in the result, not raised.
    """
    lifecycle = get_lifecycle(engine)
    if lifecycle is None:
        # Allowlist rejection — the server asked for an engine we don't own.
        return EngineUpdateResult(False, engine, f"unknown engine: {engine!r}")

    # pip channels require an explicit interpreter; npm ignores it. There is
    # deliberately no sys.executable fallback — that would install a pip engine
    # into the machine's own venv rather than the agent venv that hosts it.
    try:
        cmd = lifecycle.channel.update_argv(lifecycle.package, python=python)
    except ValueError as exc:
        return EngineUpdateResult(False, engine, str(exc))

    log.info("engine_update.start", engine=engine, kind=lifecycle.channel.kind)
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=UPDATE_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("engine_update.subprocess_failed", engine=engine, error=str(exc))
        return EngineUpdateResult(False, engine, f"install failed: {exc}")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        log.warning(
            "engine_update.failed", engine=engine, returncode=proc.returncode, tail=tail
        )
        return EngineUpdateResult(False, engine, f"exit {proc.returncode}: {tail}")

    log.info("engine_update.done", engine=engine)
    return EngineUpdateResult(True, engine, None)
