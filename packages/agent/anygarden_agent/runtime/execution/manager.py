"""Transport-free local queue, deadline, cancellation and receipt boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path

from .contracts import (
    Capabilities,
    ExecutionEvent,
    Invocation,
    Receipt,
    Runtime,
    RuntimeResult,
    SessionScope,
)
from .store import ReceiptStore


class LocalExecutionManager:
    def __init__(
        self,
        directory: Path,
        runtime: Runtime,
        *,
        authorize: Callable[[SessionScope], bool],
        queue_limit: int = 32,
    ):
        self._store = ReceiptStore(directory)
        self._runtime = runtime
        self._authorize = authorize
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False
        self._queue_limit = queue_limit
        # Exact durable attempts keep one local invocation across retries and
        # restarts. Tombstones contain no lease, credential or native handle.
        self._store.db.execute("""
            CREATE TABLE IF NOT EXISTS turn_bindings (
                turn_key TEXT PRIMARY KEY, identity TEXT NOT NULL,
                local_execution_id TEXT, cancelled INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._store.db.execute("""
            CREATE TABLE IF NOT EXISTS turn_stop_receipts (
                turn_key TEXT PRIMARY KEY, was_finished INTEGER, receipt TEXT
            )
        """)
        self._store.db.commit()

    def _invocation_cancelled(self, local_execution_id: str) -> bool:
        return self._store.db.execute(
            "SELECT 1 FROM turn_bindings WHERE local_execution_id=? AND cancelled=1",
            (local_execution_id,),
        ).fetchone() is not None

    @staticmethod
    def _turn_key(identity: dict) -> str:
        from .contracts import canonical
        from .project_turn import identity_key

        return canonical(identity_key(identity))

    def bind_turn(self, identity: dict, local_execution_id: str) -> str:
        """One invocation per delivered attempt; never replace an older ID."""
        from .contracts import canonical
        from .project_turn import ProjectTurnError, canonical_uuid

        canonical_uuid(local_execution_id)
        key = self._turn_key(identity)
        row = self._store.db.execute("SELECT * FROM turn_bindings WHERE turn_key=?", (key,)).fetchone()
        if row is not None:
            import json

            previous = json.loads(row["identity"])
            if previous["execution_id"] != identity["execution_id"] or previous["input_revision"] != identity["input_revision"]:
                raise ProjectTurnError("TURN_BINDING_CHANGED")
            if row["cancelled"]:
                raise ProjectTurnError("TURN_CANCELLED")
            if row["local_execution_id"] is not None:
                return row["local_execution_id"]
        with self._store.db:
            self._store.db.execute(
                "INSERT INTO turn_bindings(turn_key,identity,local_execution_id) VALUES(?,?,?) "
                "ON CONFLICT(turn_key) DO UPDATE SET local_execution_id=excluded.local_execution_id "
                "WHERE turn_bindings.cancelled=0 AND turn_bindings.local_execution_id IS NULL",
                (key, canonical(identity), local_execution_id),
            )
        row = self._store.db.execute("SELECT * FROM turn_bindings WHERE turn_key=?", (key,)).fetchone()
        if row["cancelled"]:
            raise ProjectTurnError("TURN_CANCELLED")
        return row["local_execution_id"]

    def turn_cancelled(self, identity: dict) -> bool:
        row = self._store.db.execute("SELECT cancelled FROM turn_bindings WHERE turn_key=?", (self._turn_key(identity),)).fetchone()
        return bool(row and row[0])

    def turn_receipt(self, identity: dict) -> Receipt | None:
        row = self._store.db.execute("SELECT local_execution_id FROM turn_bindings WHERE turn_key=?", (self._turn_key(identity),)).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            return self._store.get(row[0])
        except KeyError:
            return None

    async def stop_turn(self, identity: dict, local_execution_id: str | None, *, timeout: float = 8) -> dict:
        """Persist the tombstone before cancellation; report actual process proof."""
        import json

        from .contracts import canonical
        from .project_turn import ProjectTurnError

        key = self._turn_key(identity)
        row = self._store.db.execute("SELECT * FROM turn_bindings WHERE turn_key=?", (key,)).fetchone()
        known = row is not None
        if row is not None:
            previous = json.loads(row["identity"])
            # An initial operating turn is bound by MCP after native start.
            # The saved local ID permits only this one-way adoption.
            if previous["execution_id"] is not None and (
                previous["execution_id"] != identity["execution_id"]
                or previous["input_revision"] != identity["input_revision"]
            ):
                raise ProjectTurnError("TURN_BINDING_CHANGED")
            if local_execution_id is not None and row["local_execution_id"] not in {None, local_execution_id}:
                raise ProjectTurnError("LOCAL_EXECUTION_MISMATCH")
            local_execution_id = row["local_execution_id"] or local_execution_id
        with self._store.db:
            self._store.db.execute(
                "INSERT INTO turn_bindings VALUES(?,?,?,1) ON CONFLICT(turn_key) DO UPDATE SET "
                "identity=excluded.identity,local_execution_id=excluded.local_execution_id,cancelled=1",
                (key, canonical(identity), local_execution_id),
            )
        saved = self._store.db.execute("SELECT * FROM turn_stop_receipts WHERE turn_key=?", (key,)).fetchone()
        if saved is not None and saved["receipt"] is not None:
            cached = json.loads(saved["receipt"])
            if cached["status"] != "unknown":
                return cached

        def persist(result):
            with self._store.db:
                self._store.db.execute(
                    "INSERT INTO turn_stop_receipts(turn_key,receipt) VALUES(?,?) "
                    "ON CONFLICT(turn_key) DO UPDATE SET receipt=excluded.receipt",
                    (key, canonical(result)),
                )
            return result

        result = {"local_execution_id": local_execution_id, "status": "unknown",
                  "process_state": "unknown", "outcome": "unknown", "code": "LOCAL_EXECUTION_UNKNOWN"}
        if local_execution_id is None:
            return persist({**result, "status": "not_started", "process_state": "not_started",
                            "outcome": "cancelled", "code": None})
        try:
            before = self._store.get(local_execution_id)
        except KeyError:
            # A binding created by an earlier unknown stop is not proof that
            # this process prepared the invocation. Never promote missing
            # persisted evidence to not_started just because stop was replayed.
            unknown_origin = saved is not None and saved["receipt"] is not None and json.loads(saved["receipt"]).get("code") == "LOCAL_EXECUTION_UNKNOWN"
            if known and not unknown_origin:
                return persist({**result, "status": "not_started", "process_state": "not_started",
                                "outcome": "cancelled", "code": None})
            return persist(result)
        was_finished = bool(saved["was_finished"]) if saved is not None and saved["was_finished"] is not None else before.outcome is not None
        with self._store.db:
            self._store.db.execute(
                "INSERT INTO turn_stop_receipts(turn_key,was_finished) VALUES(?,?) "
                "ON CONFLICT(turn_key) DO UPDATE SET was_finished=COALESCE(turn_stop_receipts.was_finished,excluded.was_finished)",
                (key, int(was_finished)),
            )
        await self.owned_cancel(local_execution_id)
        task = self._tasks.get(local_execution_id)
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=timeout)
        receipt = self.owned_receipt(local_execution_id)
        result.update(process_state=receipt.process_state, outcome=receipt.outcome)
        if receipt.outcome is None or receipt.process_state in {"running", "unknown"}:
            result.update(process_state="unknown", code="STOP_UNCONFIRMED")
        elif receipt.process_state == "not_started":
            result.update(status="not_started", code=None)
        elif was_finished:
            result.update(status="already_finished", process_state="finished", code=None)
        elif receipt.process_state in {"stopped", "finished"}:
            result.update(status="confirmed", process_state="stopped", code=None)
        return persist(result)

    def capabilities(self) -> Capabilities:
        return self._runtime.capabilities()

    async def start(self, invocation: Invocation) -> Receipt:
        if self._closed:
            raise RuntimeError("execution manager is closed")
        invocation.validate()
        if self._invocation_cancelled(invocation.execution_id):
            from .project_turn import ProjectTurnError

            raise ProjectTurnError("TURN_CANCELLED")
        if not self._authorize(invocation.scope):
            raise PermissionError("local execution permission denied")
        # Snapshot the caller-owned mapping before fingerprinting and scheduling.
        invocation = replace(invocation, environment=dict(invocation.environment))
        try:
            self._store.get(invocation.execution_id)
        except KeyError:
            if len(self._tasks) >= self._queue_limit:
                raise RuntimeError("execution queue full")
        if self._store.accept(invocation):
            task = asyncio.create_task(self._run(invocation))
            self._tasks[invocation.execution_id] = task
            task.add_done_callback(
                lambda _: self._tasks.pop(invocation.execution_id, None)
            )
        return self._store.get(invocation.execution_id)

    async def _run(self, invocation: Invocation) -> None:
        key, execution_id = invocation.scope.key, invocation.execution_id
        try:
            async with self._locks.setdefault(key, asyncio.Lock()):
                if self._store.get(execution_id).state == "cancel_requested" or self._invocation_cancelled(execution_id):
                    result = RuntimeResult(
                        "cancelled", "not_started", "cancelled_before_start"
                    )
                elif not self._authorize(invocation.scope) or self._store.scope_blocked(
                    key
                ):
                    result = RuntimeResult("failed", "not_started", "POLICY_DENIED")
                else:
                    self._store.transition(execution_id, "launching", "unknown")

                    def emit(kind: str, payload: dict):
                        with self._store.db:
                            self._store.event(execution_id, kind, payload)

                    def launched(pid: int):
                        self._store.transition(execution_id, "running", "running", pid)

                    result = await self._runtime.run(
                        invocation,
                        self._store.session(key),
                        emit,
                        launched,
                        lambda: (
                            self._authorize(invocation.scope)
                            and self._store.get(execution_id).state
                            != "cancel_requested"
                        ),
                    )
                if (
                    self._store.get(execution_id).state == "cancel_requested"
                    and result.outcome == "succeeded"
                ):
                    result = replace(result, outcome="cancelled", process_state="stopped",
                                     reason="completed_after_cancel", text=None)
                self._store.finish(execution_id, result)
        except asyncio.CancelledError:
            # Cancellation before runtime start has no child. Runtime.run must
            # consume cancellation only after verifying termination of its tree.
            receipt = self._store.get(execution_id)
            unstarted = receipt.process_state == "not_started"
            self._store.finish(
                execution_id,
                RuntimeResult(
                    "cancelled" if unstarted else "unknown",
                    "not_started" if unstarted else "unknown",
                    "cancelled_before_start" if unstarted else "unconfirmed_cancel",
                ),
            )
        except Exception:  # noqa: BLE001 — closed outcome at runtime boundary
            # Do not publish arbitrary exception strings (may contain secrets).
            self._store.finish(
                execution_id, RuntimeResult("unknown", "unknown", "runtime_error")
            )

    def import_legacy_session(self, scope: SessionScope, handle: str | None, source: str) -> None:
        """One-time local upgrade of an existing room resume handle.

        Remember consumption independently of scope so a later generation or
        endpoint change cannot resurrect an old, unfenced native session.
        """
        if not self._authorize(scope):
            raise PermissionError("local session import denied")
        with self._store.db:
            self._store.db.execute(
                "CREATE TABLE IF NOT EXISTS legacy_imports (source TEXT PRIMARY KEY)"
            )
            if self._store.db.execute(
                "INSERT OR IGNORE INTO legacy_imports VALUES (?)", (source,)
            ).rowcount and handle:
                self._store.db.execute(
                    "INSERT OR IGNORE INTO sessions VALUES (?,?)", (scope.key, handle)
                )

    async def events(
        self, execution_id: str, *, after: int = 0
    ) -> AsyncIterator[ExecutionEvent]:
        self._check_access(execution_id)
        while True:
            self._check_access(execution_id)
            batch = self._store.events(execution_id, after)
            for event in batch:
                self._check_access(execution_id)
                after = event.sequence
                yield event
            if batch:
                continue
            if self._store.get(execution_id).outcome is not None:
                return
            await asyncio.sleep(0.02)

    def _check_access(self, execution_id: str) -> None:
        if not self._authorize(self._store.scope_for(execution_id)):
            raise PermissionError("local receipt access denied")

    async def cancel(self, execution_id: str) -> Receipt:
        self._check_access(execution_id)
        return await self._cancel(execution_id)

    async def _cancel(self, execution_id: str) -> Receipt:
        receipt = self._store.get(execution_id)
        if receipt.outcome is not None or receipt.state == "cancel_requested":
            return receipt
        self._store.transition(execution_id, "cancel_requested", receipt.process_state)
        task = self._tasks.get(execution_id)
        if task is not None:
            # A queued task may not have entered its coroutine yet, so let it
            # run the cancellation handler before injecting cancellation.
            await asyncio.sleep(0)
            if not task.done():
                task.cancel()
        return self._store.get(execution_id)

    async def reconcile(self, execution_id: str) -> Receipt:
        self._check_access(execution_id)
        return self._store.get(execution_id)

    def owned_receipt(self, execution_id: str) -> Receipt:
        """Trusted local owner can inspect stop proof after policy revocation.

        Transport callers must independently validate the saved execution owner
        and fingerprint. This never authorizes new execution or session access.
        """
        return self._store.get(execution_id)

    async def owned_cancel(self, execution_id: str) -> Receipt:
        """Trusted local owner can stop an execution after its grant is revoked."""
        return await self._cancel(execution_id)

    async def revoke(self, scope: SessionScope) -> None:
        """Trusted local policy owner retires a binding and cancels its executions.

        This control operation deliberately works after authorize() becomes false.
        The owner must increment policy_epoch before granting later work.
        """
        with self._store.db:
            self._store.db.execute("DELETE FROM sessions WHERE scope=?", (scope.key,))
        for execution_id in list(self._tasks):
            if self._store.scope_for(execution_id) == scope:
                await self._cancel(execution_id)

    async def close(self) -> None:
        self._closed = True
        for execution_id in list(self._tasks):
            await self._cancel(execution_id)
        await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)
        self._store.close()
