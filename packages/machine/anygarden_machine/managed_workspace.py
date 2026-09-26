"""Bounded, read-only access to runtime-reported managed working directories.

All filesystem access stays on the execution machine. POSIX descriptor walks
pin every directory and refuse symlinks, including parents and the agent root.
No external workspace capability is provided by this module.
"""

from __future__ import annotations

import errno
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from anygarden_machine.agent_dir import AgentFilePathError, validate_agent_id

CAPABILITY = "managed_workspace_browse_v1"
RECEIPT_NAME = ".anygarden-workspace.json"
PAGE_SIZE = 200
SCAN_LIMIT = 10_000
PREVIEW_BYTES = 64 * 1024
SAFE_DOTFILES = frozenset({".gitignore", ".gitattributes", ".editorconfig"})
PRIVATE_NAMES = frozenset(
    {
        "auth.json",
        "credentials",
        "credentials.json",
        "credentials.toml",
        "manifest.json",
        "runtime.json",
        "profile.yaml",
        "profile.yml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "machine.token",
        "machine.toml",
    }
)


class RuntimeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = Field(alias="schema")
    workspace: Literal[".", "workspace"]
    pid: int = Field(gt=0)
    started_at: float = Field(gt=0)
    generation: int = Field(ge=0)
    engine: Literal["codex-cli", "pi-cli"]
    permission_level: Literal["restricted", "standard", "trusted"]
    reported_at: datetime


class WorkspaceEntry(BaseModel):
    name: str = Field(max_length=255)
    kind: Literal["directory", "file", "link", "unavailable"]
    size: int | None = Field(default=None, ge=0)


class WorkspaceSnapshot(BaseModel):
    status: Literal[
        "ready",
        "not_ready",
        "stale",
        "unsupported",
        "blocked",
        "not_found",
        "too_many_entries",
    ]
    cwd: str | None = Field(default=None, max_length=4096)
    engine: Literal["codex-cli", "pi-cli"] | None = None
    permission_level: Literal["restricted", "standard", "trusted"] | None = None
    reported_at: str | None = Field(default=None, max_length=64)
    runtime_generation: int | None = Field(default=None, ge=0)
    live: bool = False
    path: str = Field(default="", max_length=1024)
    entries: list[WorkspaceEntry] = Field(default_factory=list, max_length=PAGE_SIZE)
    next_cursor: str | None = Field(default=None, max_length=255)
    text: str | None = Field(default=None, max_length=PREVIEW_BYTES)
    preview_status: Literal["text", "binary", "too_large"] | None = None

    @field_validator("reported_at")
    @classmethod
    def valid_reported_at(cls, value: str | None) -> str | None:
        if value is not None and datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("reported timestamp must include a timezone")
        return value


def supported() -> bool:
    return os.name == "posix" and all(
        hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")
    )


def private_name(name: str) -> bool:
    lower = name.lower()
    return (
        (name.startswith(".") and lower not in SAFE_DOTFILES)
        or lower in PRIVATE_NAMES
        or lower.startswith(("credentials.", "auth.", "secrets.", ".env"))
        or lower.endswith((".env", ".pem", ".key", ".p12", ".pfx", ".keystore"))
    )


def path_parts(path: str) -> list[str]:
    if path == "":
        return []
    parts = path.split("/")
    if (
        len(path) > 1024
        or len(parts) > 32
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
    ):
        raise ValueError("invalid path")
    if any(
        part in ("", ".", "..") or len(part) > 255 or private_name(part)
        for part in parts
    ):
        raise ValueError("unavailable path")
    return parts


@contextmanager
def _directory(base: int, parts: list[str]):
    fd = os.dup(base)
    try:
        for part in parts:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _root_directory(root: Path):
    # absolute() deliberately does not resolve symlinks before the safe walk.
    absolute = root.absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with _directory(fd, list(absolute.parts[1:])) as root_fd:
            yield root_fd
    finally:
        os.close(fd)


def _regular_bytes(directory: int, name: str, limit: int) -> tuple[bytes | None, int]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("not a private regular file")
        if info.st_size > limit:
            return None, info.st_size
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(8192, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        return (bytes(data) if len(data) <= limit else None), info.st_size
    finally:
        os.close(fd)


def browse(
    agents_root: Path,
    agent_id: str,
    generation: int,
    *,
    operation: Literal["list", "read"] = "list",
    path: str = "",
    cursor: str | None = None,
    running_generation: int | None = None,
    running_pid: int | None = None,
    running_started_at: float | None = None,
) -> WorkspaceSnapshot:
    """Return only safe, bounded data from this machine's managed root."""
    result = WorkspaceSnapshot(status="not_ready", path=path)
    if not supported():
        result.status = "unsupported"
        return result
    try:
        validate_agent_id(agent_id)
        parts = path_parts(path)
        if cursor is not None and (
            not cursor
            or len(cursor) > 255
            or "/" in cursor
            or "\\" in cursor
            or any(ord(c) < 32 for c in cursor)
        ):
            raise ValueError("invalid cursor")
        with (
            _root_directory(agents_root) as roots_fd,
            _directory(roots_fd, [agent_id]) as agent_fd,
        ):
            raw, _ = _regular_bytes(agent_fd, RECEIPT_NAME, 4096)
            if raw is None:
                return result
            receipt = RuntimeReceipt.model_validate(json.loads(raw))
            if receipt.reported_at.tzinfo is None:
                return result
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
                result.status = "stale"
                return result
            workspace_parts = [] if receipt.workspace == "." else ["workspace"]
            with _directory(agent_fd, workspace_parts) as workspace_fd:
                result = WorkspaceSnapshot(
                    status="ready",
                    cwd=str(
                        agents_root.absolute() / agent_id / Path(receipt.workspace)
                    ),
                    engine=receipt.engine,
                    permission_level=receipt.permission_level,
                    reported_at=receipt.reported_at.isoformat(),
                    runtime_generation=receipt.generation,
                    live=live,
                    path=path,
                )
                if operation == "read":
                    if not parts:
                        raise ValueError("file required")
                    with _directory(workspace_fd, parts[:-1]) as parent:
                        content, _ = _regular_bytes(parent, parts[-1], PREVIEW_BYTES)
                    if content is None:
                        result.preview_status = "too_large"
                    elif b"\0" in content:
                        result.preview_status = "binary"
                    else:
                        try:
                            result.text = content.decode("utf-8")
                            result.preview_status = "text"
                        except UnicodeDecodeError:
                            result.preview_status = "binary"
                else:
                    with _directory(workspace_fd, parts) as folder:
                        entries: list[WorkspaceEntry] = []
                        with os.scandir(folder) as iterator:
                            for index, entry in enumerate(iterator):
                                if index >= SCAN_LIMIT:
                                    result.status = "too_many_entries"
                                    return result
                                if private_name(entry.name) or any(
                                    ord(c) < 32 or ord(c) == 127 for c in entry.name
                                ):
                                    continue
                                if cursor is not None and entry.name <= cursor:
                                    continue
                                try:
                                    info = entry.stat(follow_symlinks=False)
                                    kind = (
                                        "link"
                                        if stat.S_ISLNK(info.st_mode)
                                        else "directory"
                                        if stat.S_ISDIR(info.st_mode)
                                        else "file"
                                        if stat.S_ISREG(info.st_mode)
                                        and info.st_nlink == 1
                                        else "unavailable"
                                    )
                                    entries.append(
                                        WorkspaceEntry(
                                            name=entry.name,
                                            kind=kind,
                                            size=info.st_size
                                            if kind == "file"
                                            else None,
                                        )
                                    )
                                except OSError:
                                    continue  # A concurrently removed entry is absent.
                        entries.sort(key=lambda entry: entry.name)
                        result.entries = entries[:PAGE_SIZE]
                        if len(entries) > PAGE_SIZE:
                            result.next_cursor = result.entries[-1].name
                return result
    except (AgentFilePathError, ValueError, ValidationError):
        result.status = "blocked"
    except OSError as exc:
        result.status = (
            "not_found"
            if exc.errno == errno.ENOENT and result.cwd
            else "not_ready"
            if exc.errno == errno.ENOENT
            else "blocked"
        )
    return result
