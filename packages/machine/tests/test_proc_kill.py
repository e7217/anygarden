"""Tests for ``doorae_machine.proc_kill``.

Verifies that ``terminate_tree`` reaches grandchildren and tolerates
already-dead processes. The tests spawn Python subprocesses that fork
their own children, then assert the full tree is gone after the
helper returns.
"""

from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from doorae_machine.proc_kill import subprocess_group_kwargs, terminate_tree


def _spawn_tree() -> subprocess.Popen[bytes]:
    """Start a parent that spawns a long-lived child of its own.

    The script prints the child PID on stdout so the test can verify
    both processes are reaped.
    """
    code = (
        "import os, sys, time, subprocess;"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        "sys.stdout.write(str(child.pid) + '\\n'); sys.stdout.flush();"
        "time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        **subprocess_group_kwargs(),
    )
    # Wait for the child PID to be announced, with a generous bound so
    # CI cold-start doesn't flake.
    assert proc.stdout is not None
    line = proc.stdout.readline()
    child_pid = int(line.strip())
    return proc, child_pid  # type: ignore[return-value]


class TestTerminateTree:
    def test_kills_root_and_children(self) -> None:
        proc, child_pid = _spawn_tree()  # type: ignore[misc]
        try:
            assert psutil.pid_exists(proc.pid)
            assert psutil.pid_exists(child_pid)

            terminate_tree(proc.pid, timeout=5.0)

            # Both should be gone shortly after; psutil.wait_procs in
            # the helper already waited, so a tight retry covers OS
            # reap latency only.
            deadline = time.time() + 3.0
            while time.time() < deadline and (
                psutil.pid_exists(proc.pid) or psutil.pid_exists(child_pid)
            ):
                time.sleep(0.05)

            assert not psutil.pid_exists(proc.pid)
            assert not psutil.pid_exists(child_pid)
        finally:
            # Defensive cleanup if the helper failed.
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_no_such_process_is_ignored(self) -> None:
        # PID 0 / huge unlikely PID — must not raise.
        terminate_tree(2_000_000_000, timeout=0.5)

    def test_already_dead_root(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
        )
        proc.wait(timeout=5)
        # Should be a no-op, no exception.
        terminate_tree(proc.pid, timeout=0.5)


class TestSubprocessGroupKwargs:
    def test_posix_returns_start_new_session(self) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX-only test")
        kwargs = subprocess_group_kwargs()
        assert kwargs == {"start_new_session": True}

    def test_win_returns_creationflags(self) -> None:
        if sys.platform != "win32":
            pytest.skip("Windows-only test")
        kwargs = subprocess_group_kwargs()
        assert "creationflags" in kwargs
