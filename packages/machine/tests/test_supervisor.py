"""watch_process against real child processes spawned with piped stdio,
exactly as ``Spawner`` launches agents (stdout/stderr ``PIPE``)."""

from __future__ import annotations

import asyncio
import sys

import pytest

from anygarden_machine.supervisor import STDERR_TAIL_MAX, watch_process

# Well past asyncio's StreamReader pause threshold (2 * 64KiB) plus the
# kernel socket buffer, so an undrained pipe is guaranteed to fill.
_FLOOD_BYTES = 2 * 1024 * 1024


async def _spawn(code: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


class _Recorder:
    def __init__(self) -> None:
        self.stopped: list[tuple[str, int]] = []
        self.crashed: list[tuple[str, int, str]] = []

    async def on_stopped(self, agent_id: str, exit_code: int) -> None:
        self.stopped.append((agent_id, exit_code))

    async def on_crashed(self, agent_id: str, exit_code: int, tail: str) -> None:
        self.crashed.append((agent_id, exit_code, tail))


@pytest.mark.asyncio
async def test_verbose_child_is_not_blocked_by_undrained_stdout() -> None:
    """A chatty agent must be able to keep writing logs and exit normally."""
    proc = await _spawn(
        "import sys\n"
        f"sys.stdout.write('x' * {_FLOOD_BYTES})\n"
        "sys.stdout.flush()\n"
    )
    rec = _Recorder()

    await asyncio.wait_for(
        watch_process("a1", proc, rec.on_stopped, rec.on_crashed), timeout=15
    )

    assert rec.stopped == [("a1", 0)]
    assert rec.crashed == []


@pytest.mark.asyncio
async def test_crash_reports_the_end_of_a_long_stderr() -> None:
    """The crash reason is the *last* stderr output (e.g. a traceback),
    bounded to STDERR_TAIL_MAX, even after megabytes of earlier noise."""
    proc = await _spawn(
        "import sys\n"
        f"sys.stderr.write('n' * {_FLOOD_BYTES})\n"
        "sys.stderr.write('\\nFATAL: quota exceeded (429)\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(3)\n"
    )
    rec = _Recorder()

    await asyncio.wait_for(
        watch_process("a1", proc, rec.on_stopped, rec.on_crashed), timeout=15
    )

    assert rec.stopped == []
    assert len(rec.crashed) == 1
    agent_id, exit_code, tail = rec.crashed[0]
    assert (agent_id, exit_code) == ("a1", 3)
    assert tail.endswith("FATAL: quota exceeded (429)\n")
    assert len(tail.encode()) <= STDERR_TAIL_MAX


@pytest.mark.asyncio
async def test_short_stderr_is_reported_whole() -> None:
    proc = await _spawn("import sys; sys.stderr.write('boom\\n'); sys.exit(1)")
    rec = _Recorder()

    await asyncio.wait_for(
        watch_process("a1", proc, rec.on_stopped, rec.on_crashed), timeout=15
    )

    assert rec.crashed == [("a1", 1, "boom\n")]


@pytest.mark.asyncio
async def test_killed_child_is_reported_after_its_output_flooded_the_pipe() -> None:
    """Once a pipe is full the child hangs; the node then kills it. The
    watcher must still observe the exit (asyncio only resolves
    ``Process.wait()`` after every pipe reaches EOF)."""
    proc = await _spawn(
        "import sys\n"
        "while True:\n"
        "    sys.stdout.write('y' * 65536)\n"
        "    sys.stdout.flush()\n"
    )
    rec = _Recorder()
    watcher = asyncio.create_task(
        watch_process("a1", proc, rec.on_stopped, rec.on_crashed)
    )

    await asyncio.sleep(0.5)
    proc.kill()
    await asyncio.wait_for(watcher, timeout=15)

    assert rec.stopped == []
    assert [(a, code) for a, code, _ in rec.crashed] == [("a1", -9)]
