"""Process-lifetime ownership and nonce-bound graceful stop, without PID signals."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

from anygarden_machine.safefs import safe_write_text, secure_chmod


class NodeOwnershipError(RuntimeError):
    """The node is already owned or requires operator recovery."""


class NodeAlreadyRunningError(NodeOwnershipError):
    """The OS lock is held by another owner."""


def read_object(path: Path) -> dict:
    if path.is_symlink():
        raise NodeOwnershipError(f"Refusing symlink: {path}")
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError) as exc:
        raise NodeOwnershipError(f"Unreadable node state: {path}") from exc


def write_object(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise NodeOwnershipError(f"Refusing symlink: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        safe_write_text(temporary, json.dumps(value) + "\n", mode=0o600)
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


class NodeLock:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.fd: int | None = None

    def acquire(self) -> None:
        if self.data_dir.is_symlink():
            raise NodeOwnershipError("Node data directory must not be a symlink")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        secure_chmod(self.data_dir, 0o700)
        path = self.data_dir / "node.lock"
        if path.is_symlink():
            raise NodeOwnershipError("Node lock must not be a symlink")
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
            raise NodeAlreadyRunningError(
                f"Node already running for {self.data_dir}; integrated mode requires one worker"
            ) from exc
        self.fd = fd

    def release(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class NodeOwner:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir.absolute()
        self.lock = NodeLock(self.data_dir)
        self.identity: dict = {}
        self.nonce = str(uuid4())
        self.acquired = False

    def acquire(self) -> None:
        self.lock.acquire()
        try:
            state_path = self.data_dir / "node-owner.json"
            identity_path = self.data_dir / "node-identity.json"
            if identity_path.exists() and not state_path.exists():
                raise NodeOwnershipError(
                    "Node recovery required: ownership receipt is missing"
                )
            if state_path.exists():
                if not identity_path.exists():
                    raise NodeOwnershipError(
                        "Node recovery required: node identity is missing"
                    )
                previous = read_object(state_path)
                if previous.get("state") != "stopped":
                    raise NodeOwnershipError(
                        "Node recovery required: previous shutdown was not confirmed. "
                        "Verify and stop residual agent process trees before repairing "
                        f"{state_path}; see docs/runbook/local-node.md. No agents were started."
                    )
            if identity_path.exists():
                self.identity = read_object(identity_path)
                for key in ("node_id", "machine_id"):
                    UUID(self.identity[key])
                if self.identity["node_id"] == self.identity["machine_id"]:
                    raise ValueError("node and machine identities must be distinct")
            else:
                self.identity = {"node_id": str(uuid4()), "machine_id": str(uuid4())}
                write_object(identity_path, self.identity)
            self.acquired = True
            self.write_state("starting")
        except BaseException:
            self.acquired = False
            self.lock.release()
            raise

    def write_state(self, state: str) -> None:
        write_object(
            self.data_dir / "node-owner.json",
            {
                "nonce": self.nonce,
                "pid": os.getpid(),
                "state": state,
                "node_id": self.identity["node_id"],
            },
        )

    def stop_requested(self) -> bool:
        path = self.data_dir / "node-stop.json"
        if not path.exists():
            return False
        try:
            return read_object(path).get("nonce") == self.nonce
        except NodeOwnershipError:
            return False

    def release(self, *, clean: bool) -> None:
        try:
            if self.acquired and clean:
                self.write_state("stopped")
        finally:
            self.acquired = False
            self.lock.release()


def stop_node(data_dir: Path, *, timeout: float = 30) -> None:
    """Ask the current owner to stop and wait for its OS lock to be released."""
    data_dir = data_dir.absolute()
    if data_dir.is_symlink():
        raise NodeOwnershipError("Node data directory must not be a symlink")
    if not data_dir.is_dir():
        raise NodeOwnershipError(f"No node data directory: {data_dir}")
    state = read_object(data_dir / "node-owner.json")
    probe = NodeLock(data_dir)
    try:
        probe.acquire()
    except NodeAlreadyRunningError:
        pass  # A live owner must also match the nonce below.
    else:
        probe.release()
        if state.get("state") == "stopped":
            return
        raise NodeOwnershipError(
            "No live owner; unconfirmed shutdown requires recovery"
        )
    nonce = state.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise NodeOwnershipError("Invalid node owner nonce")
    write_object(data_dir / "node-stop.json", {"nonce": nonce})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = read_object(data_dir / "node-owner.json")
        if current.get("nonce") != nonce:
            raise NodeOwnershipError(
                "Node owner changed during stop; new owner was not stopped"
            )
        try:
            probe.acquire()
        except NodeAlreadyRunningError:
            time.sleep(0.1)
            continue
        try:
            final = read_object(data_dir / "node-owner.json")
            if final.get("nonce") == nonce and final.get("state") == "stopped":
                return
            raise NodeOwnershipError(
                "Node exited without confirmed child cleanup; recovery required"
            )
        finally:
            probe.release()
    raise NodeOwnershipError(
        "Graceful stop timed out; no force-kill or retry was performed"
    )
