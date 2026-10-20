"""Unpacking work files moved from another machine (#915).

When an agent moves, its old machine packs the work directory into a zip
(:func:`workspace_transfer.build_archive`) and the new machine stores it at
``<agent_root>/.anygarden-import/workspace.zip``. The materializer unpacks
it into the engine's work directory just before the agent starts.

Every member name is checked with the work-file browser's rules (no
``..``, absolute paths, private or instruction names); links and special
files refuse the whole import. Files are created next to existing ones
without ever replacing them: a name that already exists is skipped and
reported.
"""

from __future__ import annotations

import os
import stat
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from anygarden.machine.managed_workspace import _directory, _existing_kind, _root_directory, path_parts

IMPORT_DIR = ".anygarden-import"
IMPORT_ZIP = "workspace.zip"
CHUNK_BYTES = 256 * 1024


class ImportRefused(Exception):
    """The archive or the target cannot be imported safely."""


@dataclass
class ImportResult:
    files: int = 0
    skipped: list[str] = field(default_factory=list)


def work_dir_name(engine: str) -> str:
    """The agent's work directory relative to its root (see the spawner)."""
    return "workspace" if engine == "codex-cli" else "."


def import_zip_path(agent_root: Path) -> Path:
    return agent_root / IMPORT_DIR / IMPORT_ZIP


@contextmanager
def open_target(directory: Path) -> Iterator[int]:
    """Open the work directory without following a link anywhere on the way."""
    try:
        with _root_directory(directory) as fd:
            yield fd
    except OSError as exc:
        raise ImportRefused(f"work directory unavailable: {exc}") from exc


def _members(archive: zipfile.ZipFile, *, max_entries: int, max_bytes: int):
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > max_entries:
        raise ImportRefused("too many files")
    if sum(info.file_size for info in infos) > max_bytes:
        raise ImportRefused("too large")
    members = []
    for info in infos:
        # Only the type bits matter; plain entries carry permissions alone.
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind and kind != stat.S_IFREG:
            raise ImportRefused(f"{info.filename!r} is not a regular file")
        try:
            parts = path_parts(info.filename)
        except ValueError as exc:
            raise ImportRefused(f"unsafe name {info.filename!r}") from exc
        if not parts:
            raise ImportRefused("empty name")
        members.append((info, parts))
    return members


def conflicts(archive_path: Path, target_fd: int) -> list[str]:
    """Names in the archive that already exist in the work directory."""
    found: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        for info, parts in _members(archive, max_entries=10**9, max_bytes=2**62):
            try:
                with _directory(target_fd, parts[:-1]) as parent:
                    if _existing_kind(parent, parts[-1]) is not None:
                        found.append(info.filename)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                found.append(info.filename)
    return found


def _ensure_directory(base: int, parts: list[str]) -> int | None:
    """Open (creating as needed) ``parts`` under ``base``; ``None`` if blocked."""
    fd = os.dup(base)
    try:
        for part in parts:
            kind = _existing_kind(fd, part)
            if kind is None:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            elif kind != "directory":
                os.close(fd)
                return None
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def extract(archive_path: Path, target_fd: int, *, max_entries: int, max_bytes: int) -> ImportResult:
    """Unpack ``archive_path`` into the open work directory."""
    result = ImportResult()
    with zipfile.ZipFile(archive_path) as archive:
        members = _members(archive, max_entries=max_entries, max_bytes=max_bytes)
        for info, parts in members:
            parent = _ensure_directory(target_fd, parts[:-1])
            if parent is None:
                result.skipped.append(info.filename)
                continue
            try:
                name = parts[-1]
                if _existing_kind(parent, name) is not None:
                    result.skipped.append(info.filename)
                    continue
                temporary = f".anygarden-import-{uuid4().hex}"
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                try:
                    written = 0
                    with os.fdopen(fd, "wb") as out, archive.open(info) as source:
                        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                            written += len(chunk)
                            if written > info.file_size:
                                raise ImportRefused(f"{info.filename!r} is larger than declared")
                            out.write(chunk)
                    try:
                        os.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                        result.files += 1
                    except FileExistsError:
                        result.skipped.append(info.filename)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
            finally:
                os.close(parent)
    return result


def apply_pending_import(agent_root: Path, engine: str) -> ImportResult | None:
    """Unpack a received import into the work directory, then remove it.

    Called by the materializer before the agent starts. A failed import is
    kept as ``failed-<id>.zip`` next to the original for inspection.
    """
    source = import_zip_path(agent_root)
    if source.is_symlink() or not source.is_file():
        return None
    target = agent_root / work_dir_name(engine)
    try:
        with open_target(target) as fd:
            return extract(source, fd, max_entries=10**6, max_bytes=2**40)
    except (ImportRefused, zipfile.BadZipFile, OSError):
        source.rename(source.with_name(f"failed-{uuid4().hex}.zip"))
        raise
    finally:
        source.unlink(missing_ok=True)
