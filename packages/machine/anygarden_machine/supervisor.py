"""Child process watchdog: monitors agent subprocesses and reports status."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Callable, Coroutine

import structlog

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

STDERR_TAIL_MAX = 2048  # 2KB max for stderr capture
_DRAIN_CHUNK = 65536
_DRAIN_SETTLE_TIMEOUT = 5.0


async def _drain(stream: asyncio.StreamReader | None, keep: bytearray | None = None) -> None:
    """Consume a child pipe until EOF, keeping at most the last
    ``STDERR_TAIL_MAX`` bytes in ``keep`` when given.

    An unread pipe blocks the child on write once asyncio's reader buffer
    and the kernel buffer fill (~100-200KB), and ``Process.wait()`` only
    resolves after every pipe reaches EOF — so without draining, a chatty
    agent freezes and its exit is never observed.
    """
    if stream is None:
        return
    while chunk := await stream.read(_DRAIN_CHUNK):
        if keep is not None:
            keep += chunk
            del keep[:-STDERR_TAIL_MAX]


async def watch_process(
    agent_id: str,
    proc: asyncio.subprocess.Process,
    on_stopped: Callable[[str, int], Coroutine],
    on_crashed: Callable[[str, int, str], Coroutine],
) -> None:
    """Watch a child process until it exits.

    Drains stdout (discarded) and stderr (tail retained) for the whole
    lifetime of the child. Calls on_stopped(agent_id, exit_code) for
    normal exit (code 0), or on_crashed(agent_id, exit_code, stderr_tail)
    for abnormal exit, where stderr_tail is the last STDERR_TAIL_MAX bytes.
    """
    stderr_buf = bytearray()
    drains = [
        asyncio.create_task(_drain(proc.stdout)),
        asyncio.create_task(_drain(proc.stderr, stderr_buf)),
    ]
    try:
        exit_code = await proc.wait()
        # EOF has been fed to both readers by now; let the drains consume
        # what is still buffered so the tail includes the final output.
        await asyncio.wait(drains, timeout=_DRAIN_SETTLE_TIMEOUT)
    except Exception as exc:
        log.error("watch_process_error", agent_id=agent_id, error=str(exc))
        await on_crashed(agent_id, -1, str(exc))
        return
    finally:
        for task in drains:
            task.cancel()

    if exit_code == 0:
        log.info("agent_stopped", agent_id=agent_id, exit_code=exit_code)
        await on_stopped(agent_id, exit_code)
    else:
        stderr_tail = stderr_buf.decode(errors="replace")
        log.warning(
            "agent_crashed",
            agent_id=agent_id,
            exit_code=exit_code,
            stderr_len=len(stderr_tail),
        )
        await on_crashed(agent_id, exit_code, stderr_tail)
