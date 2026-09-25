"""Private, agent-local Pi API-key enrollment and auth-check result mapping."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

if os.name == "posix":
    import fcntl

CONFIG_KEY = "AG_PI_NATIVE_AUTH_CONFIG"
INPUT_KEY = "AG_PI_NATIVE_AUTH_KEY"
MARKER = ".anygarden-pi-auth.json"


@dataclass(frozen=True)
class NativePiAuth:
    provider: str
    revision: int


def parse_native_auth(
    config: str | None,
    key: str | None,
    *,
    provider: str | None,
    engine: str,
    endpoint: object,
) -> NativePiAuth | None:
    if config is None and key is None:
        return None
    try:
        data = json.loads(config) if config is not None else None
        if (
            engine != "pi-cli"
            or endpoint is not None
            or not isinstance(data, dict)
            or set(data) != {"provider", "revision"}
            or data["provider"] != provider
            or type(data["revision"]) is not int
            or data["revision"] < 1
            or not isinstance(key, str)
            or not key
            or len(key) > 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
        ):
            raise ValueError
        return NativePiAuth(data["provider"], data["revision"])
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid Pi native authentication configuration") from None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def materialize_native_auth(
    runtime_home: Path, auth: NativePiAuth | None, key: str | None
) -> None:
    """Atomically manage only the selected provider entry; reject file tricks."""
    if os.name != "posix":
        raise ValueError("Pi native authentication requires POSIX")
    if bool(auth) != bool(key):
        raise ValueError("Pi provider credential is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(runtime_home, flags)
    try:
        for component in (".pi", "agent"):
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        fcntl.flock(directory, fcntl.LOCK_EX)

        def read(name: str) -> bytes | None:
            try:
                fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                )
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("Pi auth must be a regular file")
                data = stream.read(65537)
                if len(data) > 65536:
                    raise ValueError("Pi auth file is too large")
                return data

        def replace(name: str, value: bytes) -> None:
            temporary = ".pi-auth-" + secrets.token_hex(12)
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(value)
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass

        raw, marked = read("auth.json"), read(MARKER)
        try:
            entries = json.loads(raw) if raw is not None else {}
            marker = json.loads(marked) if marked is not None else None
            if not isinstance(entries, dict) or (
                marker is not None
                and (
                    not isinstance(marker, dict)
                    or set(marker) != {"provider", "sha256"}
                    or not isinstance(marker["provider"], str)
                    or marker["sha256"] != _digest(entries.get(marker["provider"]))
                )
            ):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError(
                "Existing Pi auth file or managed entry was modified"
            ) from None

        stored_key = (
            "$$" + key[1:]
            if key and key.startswith("$")
            else "$!" + key[1:]
            if key and key.startswith("!")
            else key
        )
        if (
            marker is not None
            and auth is not None
            and marker["provider"] == auth.provider
            and entries.get(auth.provider) == {"type": "api_key", "key": stored_key}
        ):
            return
        if marker is not None:
            entries.pop(marker["provider"], None)
        if auth is None:
            if marker is not None:
                replace("auth.json", json.dumps(entries, sort_keys=True).encode())
                os.unlink(MARKER, dir_fd=directory)
            return
        if auth.provider in entries:
            raise ValueError("An existing Pi credential conflicts with this provider")
        # Pi treats leading $ or ! as interpolation/command syntax. Escape
        # those bytes so an API key is always interpreted literally.
        entry = {"type": "api_key", "key": stored_key}
        entries[auth.provider] = entry
        replace("auth.json", json.dumps(entries, sort_keys=True).encode())
        replace(
            MARKER,
            json.dumps({"provider": auth.provider, "sha256": _digest(entry)}).encode(),
        )
    finally:
        os.close(directory)
