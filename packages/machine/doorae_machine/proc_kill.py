"""Cross-platform process tree termination.

Background
----------
``signal.SIGTERM`` / ``os.killpg`` only exist on POSIX. Doorae kills
agent subprocesses on three paths:

1. ``spawner.kill`` — terminate a running agent (SIGTERM → 10s →
   SIGKILL).
2. ``gemini_cli`` timeout — the Gemini CLI spawns child shells (npm /
   node) that survive a plain ``proc.kill``; the whole tree must go.
3. ``e2e_*.py`` test scripts — graceful server shutdown.

This module routes all three through ``psutil``, which exposes a
single API on POSIX and Windows. ``psutil.wait_procs`` correctly
implements the SIGTERM-then-SIGKILL pattern via ``terminate()`` then
``kill()``; on Windows those map to ``TerminateProcess``, which is
the closest equivalent to a forceful kill.
"""

from __future__ import annotations

import subprocess
import sys

import psutil
import structlog

log = structlog.get_logger()


def terminate_tree(pid: int, *, timeout: float = 10.0) -> None:
    """Terminate the process at *pid* and all its descendants.

    Sends ``terminate()`` to the whole tree first, waits up to
    *timeout* seconds for graceful exit, then ``kill()`` survivors.
    Missing / already-dead processes are ignored — by the time we
    reach this code path the process tree is on its way out either
    way.

    On POSIX this maps to SIGTERM → SIGKILL. On Windows it maps to
    TerminateProcess (no graceful shutdown signal exists; the
    *timeout* still gives child cleanup handlers a window to run).
    """
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    try:
        children = root.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []

    victims = [root, *children]

    for proc in victims:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass

    _, alive = psutil.wait_procs(victims, timeout=timeout)

    for proc in alive:
        try:
            proc.kill()
            log.warning("proc_kill_force", pid=proc.pid)
        except psutil.NoSuchProcess:
            pass

    if alive:
        # Give the OS a brief moment to reap the killed processes.
        psutil.wait_procs(alive, timeout=1.0)


def subprocess_group_kwargs() -> dict[str, object]:
    """Return ``Popen`` kwargs that put the child in its own group.

    On POSIX, ``start_new_session=True`` calls ``setsid`` so the
    child becomes a session/process group leader — this is what
    makes ``terminate_tree`` able to reach grandchildren reliably.

    On Windows, ``CREATE_NEW_PROCESS_GROUP`` provides the analogous
    isolation: signals (and forced termination via the job/group)
    only affect the new group rather than walking up to the parent.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}
