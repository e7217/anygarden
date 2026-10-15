"""One daemon per agent directory (#848).

A daemon re-adopts every live process recorded under its agents root and
stops any running agent its server did not ask for. Two daemons sharing one
root would therefore adopt and then kill each other's agents. Every daemon —
the integrated node's built-in machine, a standalone ``anygarden machine
run`` and a node-managed machine link — holds this lock for as long as it
runs, so the second one fails at start instead of corrupting the first.
"""

from __future__ import annotations

import os
from pathlib import Path

from anygarden.machine.safefs import secure_chmod

LOCK_NAME = ".daemon.lock"


class AgentRootInUseError(RuntimeError):
    """Another daemon already owns this agents root."""

    def __init__(self, agents_root: Path) -> None:
        self.agents_root = agents_root
        super().__init__(
            f"Agent directory {agents_root} is already used by another daemon "
            "(an AnyGarden node or a machine daemon). To also serve another "
            "server from this computer, give this daemon its own directory: "
            "anygarden machine connect --data-dir ~/.anygarden/remote-machines/<machine-id> ... "
            "or add the connection from the node's web UI (Admin > Machines > "
            "Serve another server)."
        )


class AgentRootLock:
    """Exclusive, process-lifetime lock on ``<agents_root>/.daemon.lock``.

    The OS releases it when the process dies, so a crash never leaves a
    directory locked; the file itself is harmless.
    """

    def __init__(self, agents_root: Path) -> None:
        self.agents_root = agents_root
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        if self.agents_root.is_symlink():
            raise ValueError(f"Agent directory must not be a symlink: {self.agents_root}")
        self.agents_root.mkdir(parents=True, exist_ok=True)
        secure_chmod(self.agents_root, 0o700)
        path = self.agents_root / LOCK_NAME
        if path.is_symlink():
            raise ValueError(f"Agent directory lock must not be a symlink: {path}")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise AgentRootInUseError(self.agents_root) from exc
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
