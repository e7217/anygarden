"""Child process watchdog: monitors agent subprocesses and reports status."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Coroutine

import structlog

if TYPE_CHECKING:
    pass

log = structlog.get_logger()

STDERR_TAIL_MAX = 2048  # 2KB max for stderr capture
# Rotate the per-agent log at 5MB and keep a single previous generation,
# so an agent's history survives a restart without growing unbounded.
AGENT_LOG_MAX_BYTES = 5 * 1024 * 1024
_DRAIN_CHUNK = 65536
_DRAIN_SETTLE_TIMEOUT = 5.0


class _AgentLog:
    """Append-only sink for one agent's stdout+stderr, size-capped.

    Best-effort by design: a logging failure (bad path, full disk) must
    never take the agent down, so every error disables the sink and the
    supervision path carries on.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._rotated = path.with_name(path.name + ".1")
        self._fh = path.open("ab")
        self._size = path.stat().st_size

    def write(self, chunk: bytes) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(chunk)
            self._fh.flush()
            self._size += len(chunk)
            if self._size >= AGENT_LOG_MAX_BYTES:
                self._rotate()
        except OSError as exc:
            log.warning("agent_log_write_failed", path=str(self._path), error=str(exc))
            self.close()

    def _rotate(self) -> None:
        self._fh.close()
        os.replace(self._path, self._rotated)
        self._fh = self._path.open("ab")
        self._size = 0

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None


def _open_agent_log(agent_id: str, log_path: Path | None) -> _AgentLog | None:
    if log_path is None:
        return None
    try:
        return _AgentLog(log_path)
    except OSError as exc:
        log.warning(
            "agent_log_open_failed",
            agent_id=agent_id,
            path=str(log_path),
            error=str(exc),
        )
        return None


async def _drain(
    stream: asyncio.StreamReader | None,
    keep: bytearray | None = None,
    sink: _AgentLog | None = None,
) -> None:
    """Consume a child pipe until EOF, keeping at most the last
    ``STDERR_TAIL_MAX`` bytes in ``keep`` and mirroring every chunk into
    ``sink`` when given.

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
        if sink is not None:
            sink.write(chunk)


async def watch_process(
    agent_id: str,
    proc: asyncio.subprocess.Process,
    on_stopped: Callable[[str, int], Coroutine],
    on_crashed: Callable[[str, int, str], Coroutine],
    log_path: Path | None = None,
) -> None:
    """Watch a child process until it exits.

    Drains stdout and stderr (tail retained) for the whole lifetime of the
    child. With ``log_path`` both streams are also appended to that file,
    rotated once at ``AGENT_LOG_MAX_BYTES``, which is the only place an
    agent's own logs survive. Calls on_stopped(agent_id, exit_code) for
    normal exit (code 0), or on_crashed(agent_id, exit_code, stderr_tail)
    for abnormal exit, where stderr_tail is the last STDERR_TAIL_MAX bytes.
    """
    stderr_buf = bytearray()
    agent_log = _open_agent_log(agent_id, log_path)
    drains = [
        asyncio.create_task(_drain(proc.stdout, sink=agent_log)),
        asyncio.create_task(_drain(proc.stderr, stderr_buf, sink=agent_log)),
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
        if agent_log is not None:
            agent_log.close()

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
