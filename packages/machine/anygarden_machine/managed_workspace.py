"""Bounded access to runtime-reported managed working directories.

All filesystem access stays on the execution machine. POSIX descriptor walks
pin every directory and refuse symlinks, including parents and the agent root.
No external workspace capability is provided by this module.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from anygarden_machine.agent_dir import AgentFilePathError, validate_agent_id

CAPABILITY = "managed_workspace_browse_v1"
EDIT_CAPABILITY = "managed_workspace_edit_v1"
RECEIPT_NAME = ".anygarden-workspace.json"
PAGE_SIZE = 200
SCAN_LIMIT = 10_000
PREVIEW_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 1024 * 1024
MAX_UPLOAD_ENCODED = 4 * ((MAX_UPLOAD_BYTES + 2) // 3)
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
        "conflict",
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
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    edit_token: str | None = Field(default=None, min_length=64, max_length=64)

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


def _edit_token(receipt: RuntimeReceipt) -> str:
    return hashlib.sha256(
        f"{receipt.workspace}\0{receipt.generation}\0{receipt.reported_at.isoformat()}".encode()
    ).hexdigest()


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
                    edit_token=_edit_token(receipt),
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
                            result.sha256 = hashlib.sha256(content).hexdigest()
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


def _existing_kind(directory: int, name: str) -> str | None:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        return "file"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    return "blocked"


def _write_atomic(parent: int, name: str, content: bytes, *, create: bool) -> None:
    temporary = f".anygarden-edit-{uuid4().hex}"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            # link() is an atomic create-if-absent; replace() would overwrite.
            os.link(
                temporary,
                name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        else:
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def mutate(
    agents_root: Path,
    agent_id: str,
    generation: int,
    *,
    operation: Literal["mkdir", "upload", "write"],
    path: str,
    edit_token: str,
    content_base64: str | None = None,
    text: str | None = None,
    expected_sha256: str | None = None,
    running_generation: int | None = None,
    running_pid: int | None = None,
    running_started_at: float | None = None,
) -> WorkspaceSnapshot:
    """Change one safe entry in the actual workspace, never an external folder."""
    result = WorkspaceSnapshot(status="blocked", path=path)
    if not supported():
        result.status = "unsupported"
        return result
    try:
        validate_agent_id(agent_id)
        parts = path_parts(path)
        if not parts or len(edit_token) != 64:
            return result
        if operation == "mkdir":
            if (
                content_base64 is not None
                or text is not None
                or expected_sha256 is not None
            ):
                return result
        elif operation == "upload":
            if (
                content_base64 is None
                or text is not None
                or expected_sha256 is not None
            ):
                return result
            if len(content_base64) > MAX_UPLOAD_ENCODED:
                result.status = "too_large"
                return result
            content = base64.b64decode(content_base64, validate=True)
            if len(content) > MAX_UPLOAD_BYTES:
                result.status = "too_large"
                return result
        elif operation == "write":
            if content_base64 is not None or text is None or expected_sha256 is None:
                return result
            if expected_sha256 != "absent" and (
                len(expected_sha256) != 64
                or any(char not in "0123456789abcdef" for char in expected_sha256)
            ):
                return result
            content = text.encode("utf-8")
            if b"\0" in content:
                return result
            if len(content) > PREVIEW_BYTES:
                result.status = "too_large"
                return result
        else:
            return result

        # A fresh receipt and open directory descriptors are required even if
        # the browser's listing is old or another path was selected meanwhile.
        with (
            _root_directory(agents_root) as roots_fd,
            _directory(roots_fd, [agent_id]) as agent_fd,
        ):
            raw, _ = _regular_bytes(agent_fd, RECEIPT_NAME, 4096)
            if raw is None:
                result.status = "not_ready"
                return result
            receipt = RuntimeReceipt.model_validate(json.loads(raw))
            if _edit_token(receipt) != edit_token:
                result.status = "stale"
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
            with (
                _directory(agent_fd, workspace_parts) as workspace_fd,
                _directory(workspace_fd, parts[:-1]) as parent,
            ):
                name = parts[-1]
                kind = _existing_kind(parent, name)
                if kind == "blocked" or (
                    kind is not None
                    and kind != ("directory" if operation == "mkdir" else "file")
                ):
                    return result
                if operation == "mkdir":
                    if kind is not None:
                        result.status = "conflict"
                        return result
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                    os.fsync(parent)
                elif operation == "upload" or expected_sha256 == "absent":
                    if kind is not None:
                        result.status = "conflict"
                        return result
                    _write_atomic(parent, name, content, create=True)
                else:
                    if kind is None:
                        result.status = "not_found"
                        return result
                    current, _ = _regular_bytes(parent, name, PREVIEW_BYTES)
                    if current is None or b"\0" in current:
                        return result
                    try:
                        current.decode("utf-8")
                    except UnicodeDecodeError:
                        return result
                    if hashlib.sha256(current).hexdigest() != expected_sha256:
                        result.status = "conflict"
                        return result
                    _write_atomic(parent, name, content, create=False)
        return browse(
            agents_root,
            agent_id,
            generation,
            operation="list" if operation == "mkdir" else "read",
            path="/".join(parts[:-1]) if operation == "mkdir" else path,
            running_generation=running_generation,
            running_pid=running_pid,
            running_started_at=running_started_at,
        )
    except (
        AgentFilePathError,
        ValueError,
        ValidationError,
        UnicodeError,
        base64.binascii.Error,
    ):
        return result
    except OSError as exc:
        result.status = (
            "conflict"
            if exc.errno == errno.EEXIST
            else "not_found"
            if exc.errno == errno.ENOENT
            else "blocked"
        )
        return result
