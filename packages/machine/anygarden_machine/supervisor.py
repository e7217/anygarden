"""Child process watchdog: monitors agent subprocesses and reports status."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Coroutine

import structlog

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

STDERR_TAIL_MAX = 2048  # 2KB max for stderr capture


async def watch_process(
    agent_id: str,
    proc: asyncio.subprocess.Process,
    on_stopped: Callable[[str, int], Coroutine],
    on_crashed: Callable[[str, int, str], Coroutine],
) -> None:
    """Watch a child process until it exits.

    Calls on_stopped(agent_id, exit_code) for normal exit (code 0),
    or on_crashed(agent_id, exit_code, stderr_tail) for abnormal exit.
    """
    try:
        exit_code = await proc.wait()
    except Exception as exc:
        log.error("watch_process_error", agent_id=agent_id, error=str(exc))
        await on_crashed(agent_id, -1, str(exc))
        return

    if exit_code == 0:
        log.info("agent_stopped", agent_id=agent_id, exit_code=exit_code)
        await on_stopped(agent_id, exit_code)
    else:
        # Collect stderr tail (max 2KB)
        stderr_tail = ""
        if proc.stderr is not None:
            try:
                raw = await proc.stderr.read(STDERR_TAIL_MAX)
                stderr_tail = raw.decode(errors="replace")
            except Exception:
                stderr_tail = "(stderr read failed)"
        log.warning(
            "agent_crashed",
            agent_id=agent_id,
            exit_code=exit_code,
            stderr_len=len(stderr_tail),
        )
        await on_crashed(agent_id, exit_code, stderr_tail)
