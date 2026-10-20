"""Streaming work-file transfers on the machine (#914).

Work files larger than one machine frame travel over HTTP (see
``anygarden.workspaces.transfers``). This module holds the disk side: it
opens the agent's workspace exactly like :mod:`managed_workspace` (fresh
runtime receipt, descriptors that never follow links, private and
instruction names refused) and then streams a file out, receives one in,
or packs a folder into a zip.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from pydantic import ValidationError

from anygarden.machine.agent_dir import AgentFilePathError, validate_agent_id
from anygarden.machine.managed_workspace import (
    INSTRUCTION_NAMES,
    RECEIPT_NAME,
    RuntimeReceipt,
    _directory,
    _edit_token,
    _existing_kind,
    _regular_bytes,
    _root_directory,
    path_parts,
    private_name,
    supported,
)

CAPABILITY = "managed_workspace_transfer_v1"
CHUNK_BYTES = 256 * 1024
# Workspace root entries that a move never carries (#915): room memory and
# shared files are restored by the server; inbox/outbox are runtime only.
MEMORY_DIR = "memory"


class TransferError(Exception):
    """A transfer was refused; ``status`` uses the workspace vocabulary."""

    def __init__(self, status: str, message: str = "") -> None:
        super().__init__(message or status)
        self.status = status


@dataclass(frozen=True)
class ArchiveSummary:
    files: int
    bytes: int


@contextmanager
def open_workspace(
    agents_root: Path,
    agent_id: str,
    generation: int,
    *,
    edit_token: str | None = None,
    running_generation: int | None = None,
    running_pid: int | None = None,
    running_started_at: float | None = None,
) -> Iterator[int]:
    """Yield a descriptor of the agent's workspace, checked like ``browse``.

    ``edit_token`` (required for writes) must match the current receipt.
    """
    if not supported():
        raise TransferError("unsupported")
    try:
        validate_agent_id(agent_id)
    except AgentFilePathError as exc:
        raise TransferError("blocked") from exc
    try:
        with (
            _root_directory(agents_root) as roots_fd,
            _directory(roots_fd, [agent_id]) as agent_fd,
        ):
            raw, _ = _regular_bytes(agent_fd, RECEIPT_NAME, 4096)
            if raw is None:
                raise TransferError("not_ready")
            try:
                receipt = RuntimeReceipt.model_validate(json.loads(raw))
            except (ValidationError, ValueError) as exc:
                raise TransferError("not_ready") from exc
            if edit_token is not None and _edit_token(receipt) != edit_token:
                raise TransferError("stale")
            live = running_pid is not None
            if receipt.generation > generation or (
                live
                and (
                    receipt.generation != generation
                    or running_generation != generation
                    or receipt.pid != running_pid
                    or running_started_at is None
                    or abs(receipt.started_at - running_started_at) > 0.1
                )
            ):
                raise TransferError("stale")
            workspace_parts = [] if receipt.workspace == "." else ["workspace"]
            with _directory(agent_fd, workspace_parts) as workspace_fd:
                yield workspace_fd
    except FileNotFoundError as exc:
        raise TransferError("not_ready") from exc


def _parts(path: str, *, allow_root: bool = False) -> list[str]:
    try:
        parts = path_parts(path)
    except ValueError as exc:
        raise TransferError("blocked") from exc
    if not parts and not allow_root:
        raise TransferError("blocked")
    return parts


@contextmanager
def open_for_send(workspace_fd: int, path: str, *, max_bytes: int) -> Iterator[tuple[BinaryIO, int]]:
    """Yield ``(stream, size)`` for one regular work file."""
    parts = _parts(path)
    try:
        with _directory(workspace_fd, parts[:-1]) as parent:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError as exc:
        raise TransferError("not_found") from exc
    except OSError as exc:
        raise TransferError("blocked") from exc
    stream = os.fdopen(fd, "rb")
    with stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise TransferError("blocked")
        if info.st_size > max_bytes:
            raise TransferError("too_large")
        yield stream, info.st_size


def receive_into(
    workspace_fd: int,
    path: str,
    chunks: Iterable[bytes],
    *,
    size: int,
    sha256: str,
) -> None:
    """Create ``path`` from ``chunks`` only if size and digest match.

    The bytes go to a private temporary file first; the final name is
    created with ``link`` so an existing file is never replaced.
    """
    parts = _parts(path)
    try:
        with _directory(workspace_fd, parts[:-1]) as parent:
            name = parts[-1]
            if _existing_kind(parent, name) is not None:
                raise TransferError("conflict")
            temporary = f".anygarden-upload-{uuid4().hex}"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                digest = hashlib.sha256()
                written = 0
                with os.fdopen(fd, "wb") as out:
                    for chunk in chunks:
                        written += len(chunk)
                        if written > size:
                            raise TransferError("too_large")
                        digest.update(chunk)
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                if written != size or digest.hexdigest() != sha256:
                    raise TransferError("blocked", "size or digest mismatch")
                try:
                    os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                except FileExistsError as exc:
                    raise TransferError("conflict") from exc
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
    except FileNotFoundError as exc:
        raise TransferError("not_found") from exc
    except TransferError:
        raise
    except OSError as exc:
        raise TransferError("blocked") from exc


def _walk_files(workspace_fd: int, parts: list[str], *, exclude_memory: bool):
    """Yield ``(relative_path, directory_fd, name, size)`` for archivable files.

    Links, special files, private names and (at the workspace root) the
    instruction files are skipped. The directory descriptor is only valid
    until the next item.
    """

    def walk(directory: int, prefix: str, at_root: bool):
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            name = entry.name
            if private_name(name) or any(ord(c) < 32 or ord(c) == 127 for c in name):
                continue
            if at_root and (name in INSTRUCTION_NAMES or (exclude_memory and name == MEMORY_DIR)):
                continue
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    yield from walk(child, f"{prefix}{name}/", False)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            yield f"{prefix}{name}", directory, name, info.st_size

    with _directory(workspace_fd, parts) as folder:
        yield from walk(folder, "", not parts)


def measure(workspace_fd: int, path: str = "", *, exclude_memory: bool = False) -> ArchiveSummary:
    """Count the files and bytes :func:`build_archive` would pack (#915)."""
    parts = _parts(path, allow_root=True)
    files = total = 0
    try:
        for _rel, _directory_fd, _name, size in _walk_files(workspace_fd, parts, exclude_memory=exclude_memory):
            files += 1
            total += size
    except FileNotFoundError as exc:
        raise TransferError("not_found") from exc
    except OSError as exc:
        raise TransferError("blocked") from exc
    return ArchiveSummary(files=files, bytes=total)


def build_archive(
    workspace_fd: int,
    path: str,
    out: BinaryIO,
    *,
    max_entries: int,
    max_bytes: int,
    exclude_memory: bool = False,
) -> ArchiveSummary:
    """Write the folder ``path`` ("" = whole workspace) to ``out`` as a zip.

    Links, special files, private names and (at the workspace root) the
    instruction files are left out; with ``exclude_memory`` also
    ``memory/`` (a machine move, #915). Exceeding ``max_entries`` files or
    ``max_bytes`` uncompressed raises ``too_large``.
    """
    parts = _parts(path, allow_root=True)
    files = 0
    total = 0
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for rel, directory, name, size in _walk_files(workspace_fd, parts, exclude_memory=exclude_memory):
                files += 1
                total += size
                if files > max_entries or total > max_bytes:
                    raise TransferError("too_large")
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as source, archive.open(rel, "w") as target:
                    for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                        target.write(chunk)
    except FileNotFoundError as exc:
        raise TransferError("not_found") from exc
    except TransferError:
        raise
    except OSError as exc:
        raise TransferError("blocked") from exc
    return ArchiveSummary(files=files, bytes=total)
