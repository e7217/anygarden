"""Process group helpers shared by local CLI integrations."""

import subprocess
import sys
from typing import Any

import psutil


def _subprocess_group_kwargs() -> dict[str, Any]:
    """Cross-platform Popen kwargs that put the child in its own group.

    POSIX: ``setsid`` so children can be terminated as a tree.
    Windows: ``CREATE_NEW_PROCESS_GROUP`` for the analogous isolation.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_tree(pid: int, timeout: float) -> None:
    """Terminate *pid* and all descendants. Tolerates already-dead PIDs."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        victims = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        victims = [root]
    for proc in victims:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(victims, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
