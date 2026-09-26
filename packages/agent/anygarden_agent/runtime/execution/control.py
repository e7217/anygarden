"""Agent-owned prepare/commit boundary for its authenticated DM control channel.

Wire data never chooses a runtime, credential, path, tool or permission tier.
Prepared requests survive restart without persisting a credential environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .contracts import Invocation, Receipt, Runtime, SessionScope, canonical
from .launch import ExecutionLaunch
from .manager import LocalExecutionManager

CAPABILITY = "execution_control_v1"
LEASE_SECONDS = 20
MAX_PROMPT = 262_144
_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP", "TEMP")
_PREPARE_KEYS = frozenset(
    {
        "execution_id",
        "execution_node_id",
        "authority_node_id",
        "channel_id",
        "thread_root_id",
        "prompt",
        "instructions",
        "policy_epoch",
        "grant_epoch",
        "peer_epoch",
    }
)


class ControlError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ControlError("LOCAL_STORAGE_UNSAFE")
    path.chmod(0o700)


def _stage_codex_auth(source: Path, destination: Path) -> str:
    """Copy only JSON auth from the trusted local launch home, never config.

    An intentional machine-installed auth symlink may select the trusted host
    credential. Resolve it here, before opening the resulting regular file;
    neither endpoint is supplied by a control message.
    """
    try:
        resolved = source.resolve(strict=True)
        fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ControlError("AUTH_MISSING")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(1_048_577)
        finally:
            os.close(fd)
        if len(data) > 1_048_576 or not isinstance(json.loads(data), dict):
            raise ControlError("AUTH_MISSING")
    except (OSError, ValueError, UnicodeError) as exc:
        raise ControlError("AUTH_MISSING") from exc
    # Preserve a private CLI-refreshed credential for this binding. Never write
    # back to the host credential. Only the source digest participates in fencing.
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_file():
            raise ControlError("LOCAL_STORAGE_UNSAFE")
        destination.chmod(0o600)
    else:
        fd, name = tempfile.mkstemp(prefix=".auth-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
    return hashlib.sha256(data).hexdigest()


class AgentExecutionControl:
    """One process owns this controller and its separate strict runtime manager."""

    def __init__(
        self,
        *,
        root: Path,
        agent_id: str,
        launch: Callable[[], ExecutionLaunch],
        runtime: Runtime,
        permission_level: str = "restricted",
        instructions: str = "",
        reasoning_effort: str | None = None,
        timeout_seconds: float = 600,
        codex_auth_source: Path | None = None,
        lease_seconds: float = LEASE_SECONDS,
    ):
        if os.name != "posix" or permission_level not in {"restricted", "standard"}:
            raise ControlError("UNSUPPORTED_PERMISSION")
        self.root = root.resolve(strict=True)
        self.agent_id = agent_id
        self.launch = launch
        self.runtime = runtime
        self.permission_level = permission_level
        self.instructions = instructions
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.auth_source = codex_auth_source
        self.lease_seconds = lease_seconds
        self.environment = {
            key: os.environ[key] for key in _ENV_KEYS if key in os.environ
        }
        self.directory = self.root / ".anygarden-remote-execution"
        _private_dir(self.directory)
        self._leases: dict[str, float] = {}
        self._manager = LocalExecutionManager(
            self.directory / "receipts",
            runtime,
            authorize=self._authorized,
        )
        path = self.directory / "prepared.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS prepared (
            id TEXT PRIMARY KEY, request TEXT NOT NULL, descriptor TEXT NOT NULL,
            generation INTEGER NOT NULL, state TEXT NOT NULL
        )""")
        self._lock = asyncio.Lock()
        self._closed = False
        self._watchdog: asyncio.Task | None = None
        self._room_id: str | None = None

    def attach(self, room_id: str) -> None:
        if self._room_id not in (None, room_id):
            raise ControlError("WRONG_CONTROL_ROOM")
        self._room_id = room_id
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watch_leases())

    def _authorized(self, scope: SessionScope) -> bool:
        return (
            not self._closed
            and scope.agent_id == self.agent_id
            and self._leases.get(scope.key, 0) > asyncio.get_running_loop().time()
        )

    def _renew(self, scope: SessionScope) -> None:
        self._leases[scope.key] = asyncio.get_running_loop().time() + self.lease_seconds

    async def _watch_leases(self) -> None:
        while True:
            await asyncio.sleep(min(1, self.lease_seconds / 2))
            async with self._lock:
                for row in self.db.execute(
                    "SELECT descriptor FROM prepared WHERE state='started'"
                ):
                    scope = SessionScope(**json.loads(row[0])["scope"])
                    if not self._authorized(scope):
                        await self._revoke_scope(scope)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - _PREPARE_KEYS:
            raise ControlError("INVALID_REQUEST")
        request = dict(payload)
        for key in (
            "execution_id",
            "execution_node_id",
            "authority_node_id",
            "channel_id",
        ):
            value = request.get(key)
            if not isinstance(value, str) or not 1 <= len(value) <= 256:
                raise ControlError("INVALID_REQUEST")
        for key in ("policy_epoch", "grant_epoch", "peer_epoch"):
            value = request.get(key)
            if type(value) is not int or not 0 <= value < 2**63:
                raise ControlError("INVALID_REQUEST")
        for key in ("prompt", "instructions"):
            value = request.get(key, "")
            if not isinstance(value, str) or len(value.encode()) > MAX_PROMPT:
                raise ControlError("INVALID_REQUEST")
        if not request.get("prompt"):
            raise ControlError("INVALID_REQUEST")
        thread = request.get("thread_root_id")
        if thread is not None and (not isinstance(thread, str) or len(thread) > 256):
            raise ControlError("INVALID_REQUEST")
        return request

    def _invocation(self, request: dict[str, Any]) -> Invocation:
        launch = self.launch()
        workspace = self.root / "workspace"
        if not workspace.is_dir():
            workspace = self.root
        if workspace.is_symlink() or workspace.resolve() not in (
            self.root,
            self.root / "workspace",
        ):
            raise ControlError("LOCAL_STORAGE_UNSAFE")
        # The path is used only locally; hashes are not reversible path strings.
        binding = hashlib.sha256(str(workspace).encode()).hexdigest()
        stat_info = workspace.stat()
        epoch = int.from_bytes(
            hashlib.sha256(f"{stat_info.st_dev}:{stat_info.st_ino}".encode()).digest()[
                :7
            ],
            "big",
        )
        policy = int.from_bytes(
            hashlib.sha256(
                canonical(
                    [
                        request["policy_epoch"],
                        request["grant_epoch"],
                        request["peer_epoch"],
                        self.permission_level,
                    ]
                ).encode()
            ).digest()[:7],
            "big",
        )
        caps = self.runtime.capabilities()
        scope = SessionScope(
            request["execution_node_id"],
            self.agent_id,
            request["authority_node_id"],
            request["channel_id"],
            request.get("thread_root_id"),
            binding,
            epoch,
            policy,
            engine=launch.engine,
            engine_version=caps.engine_version,
        )
        homes = self.directory / "homes"
        _private_dir(homes)
        # Scope before launch binding is stable and contains no credentials.
        home = homes / scope.key
        _private_dir(home)
        auth_digest = ""
        if launch.engine == "codex-cli" and launch.endpoint is None:
            if self.auth_source is None:
                raise ControlError("AUTH_MISSING")
            auth_digest = _stage_codex_auth(self.auth_source, home / "auth.json")
        scope = replace(
            scope,
            policy_epoch=int.from_bytes(
                hashlib.sha256(f"{scope.policy_epoch}:{auth_digest}".encode()).digest()[
                    :7
                ],
                "big",
            ),
        )
        instructions = self.instructions
        if request.get("instructions"):
            instructions += "\n" + request["instructions"]
        return launch.bind(
            Invocation(
                execution_id=request["execution_id"],
                scope=scope,
                prompt=request["prompt"],
                workspace=workspace,
                runtime_home=home,
                instructions=instructions,
                permission_level=self.permission_level,
                reasoning_effort=self.reasoning_effort,
                timeout_seconds=self.timeout_seconds,
                environment=self.environment,
            )
        )

    def _describe(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read owned preparation after an ACK loss, without rebuilding or renewing.

        A coordinator may have only its pre-acceptance no-start tombstone after
        restart. Its synthetic fingerprint is deliberately not accepted for
        cancel; this exact-scope lookup recovers only the existing local record.
        """
        keys = {"execution_id", "execution_node_id", "authority_node_id", "channel_id"}
        if set(payload) != keys or any(
            not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 256
            for key in keys
        ):
            raise ControlError("INVALID_REQUEST")
        row = self.db.execute(
            "SELECT descriptor FROM prepared WHERE id=?", (payload["execution_id"],)
        ).fetchone()
        if row is None:
            raise ControlError("EXECUTION_UNKNOWN")
        descriptor = json.loads(row["descriptor"])
        scope = descriptor["scope"]
        if scope["agent_id"] != self.agent_id or any(
            scope[key] != payload[key]
            for key in ("execution_node_id", "authority_node_id", "channel_id")
        ):
            raise ControlError("POLICY_DENIED")
        return descriptor

    def _row(self, payload: dict[str, Any]):
        if set(payload) != {"execution_id", "fingerprint"}:
            raise ControlError("INVALID_REQUEST")
        row = self.db.execute(
            "SELECT * FROM prepared WHERE id=?", (payload.get("execution_id"),)
        ).fetchone()
        if row is None:
            raise ControlError("EXECUTION_UNKNOWN")
        descriptor = json.loads(row["descriptor"])
        if payload.get("fingerprint") != descriptor["fingerprint"]:
            raise ControlError("EXECUTION_CONFLICT")
        return row, descriptor

    @staticmethod
    def _receipt(receipt: Receipt) -> dict[str, Any]:
        # Closed allowlist; native handles and paths never appear in this DTO.
        return {
            key: value
            for key, value in asdict(receipt).items()
            if key
            in {
                "execution_id",
                "state",
                "process_state",
                "outcome",
                "text",
                "usage",
                "error_code",
            }
        }

    async def handle(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._closed or self._room_id is None:
                raise ControlError("CONTROL_DISCONNECTED")
            if action == "describe":
                return self._describe(payload)
            if action == "prepare":
                request = self._request(payload)
                invocation = self._invocation(request)
                descriptor = {
                    "execution_id": invocation.execution_id,
                    "scope": asdict(invocation.scope),
                    "fingerprint": invocation.fingerprint,
                    "generation": self.launch().generation,
                }
                row = self.db.execute(
                    "SELECT * FROM prepared WHERE id=?", (invocation.execution_id,)
                ).fetchone()
                if row:
                    if (
                        row["request"] != canonical(request)
                        or json.loads(row["descriptor"]) != descriptor
                    ):
                        raise ControlError("EXECUTION_CONFLICT")
                    if row["state"].startswith("revoked"):
                        raise ControlError("POLICY_DENIED")
                    return descriptor
                pending = self.db.execute(
                    "SELECT count(*) FROM prepared WHERE state='prepared'"
                ).fetchone()[0]
                if pending >= 32:
                    raise ControlError("EXECUTION_QUEUE_FULL")
                with self.db:
                    self.db.execute(
                        "INSERT INTO prepared VALUES (?,?,?,?,?)",
                        (
                            invocation.execution_id,
                            canonical(request),
                            canonical(descriptor),
                            self.launch().generation,
                            "prepared",
                        ),
                    )
                return descriptor
            if action not in {"start", "reconcile", "cancel", "revoke"}:
                raise ControlError("INVALID_REQUEST")
            row, descriptor = self._row(payload)
            execution_id = row["id"]
            scope = SessionScope(**descriptor["scope"])
            if action == "start":
                if row["state"].startswith("revoked"):
                    raise ControlError("POLICY_DENIED")
                if row["generation"] != self.launch().generation:
                    raise ControlError("GENERATION_CHANGED")
                invocation = self._invocation(json.loads(row["request"]))
                if invocation.fingerprint != descriptor["fingerprint"]:
                    raise ControlError("CONFIGURATION_CHANGED")
                # Durable launch intent precedes start; a crash in between is
                # unknown and never permits an automatic duplicate start.
                if row["state"] == "started":
                    try:
                        return self._receipt(self._manager.owned_receipt(execution_id))
                    except KeyError as exc:
                        raise ControlError("EXECUTION_UNKNOWN") from exc
                self._renew(scope)
                with self.db:
                    self.db.execute(
                        "UPDATE prepared SET state='started' WHERE id=?",
                        (execution_id,),
                    )
                return self._receipt(await self._manager.start(invocation))
            if action == "revoke":
                await self._revoke_scope(scope)
            elif action == "cancel":
                state = (
                    "revoked_started"
                    if row["state"] in {"started", "revoked_started"}
                    else "revoked_prepared"
                )
                with self.db:
                    self.db.execute(
                        "UPDATE prepared SET state=? WHERE id=?", (state, execution_id)
                    )
                try:
                    await self._manager.owned_cancel(execution_id)
                except KeyError:
                    pass
            elif (
                row["state"] == "started"
                and row["generation"] == self.launch().generation
            ):
                self._renew(scope)
            try:
                return self._receipt(self._manager.owned_receipt(execution_id))
            except KeyError:
                if row["state"] in {"started", "revoked_started"}:
                    return self._receipt(
                        Receipt(execution_id, "unknown", "unknown", "unknown")
                    )
                state = (
                    "cancelled"
                    if action in {"cancel", "revoke"}
                    or row["state"].startswith("revoked")
                    else "prepared"
                )
                return self._receipt(
                    Receipt(
                        execution_id,
                        state,
                        "not_started",
                        "cancelled" if state == "cancelled" else None,
                    )
                )

    async def _revoke_scope(self, scope: SessionScope) -> None:
        self._leases.pop(scope.key, None)
        with self.db:
            for row in self.db.execute(
                "SELECT id,descriptor,state FROM prepared"
            ).fetchall():
                if json.loads(row["descriptor"])["scope"] == asdict(scope):
                    state = (
                        "revoked_started"
                        if row["state"] in {"started", "revoked_started"}
                        else "revoked_prepared"
                    )
                    self.db.execute(
                        "UPDATE prepared SET state=? WHERE id=?", (state, row["id"])
                    )
        await self._manager.revoke(scope)

    async def disconnected(self, room_id: str) -> None:
        if self._room_id != room_id:
            return
        async with self._lock:
            self._room_id = None
            self._leases.clear()
            for row in self.db.execute(
                "SELECT descriptor FROM prepared WHERE state='started'"
            ).fetchall():
                await self._revoke_scope(SessionScope(**json.loads(row[0])["scope"]))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._watchdog:
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)
        await self._manager.close()
        self.db.close()
