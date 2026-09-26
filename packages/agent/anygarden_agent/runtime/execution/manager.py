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

    def capabilities(self) -> Capabilities:
        return self._runtime.capabilities()

    async def start(self, invocation: Invocation) -> Receipt:
        if self._closed:
            raise RuntimeError("execution manager is closed")
        invocation.validate()
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
                if self._store.get(execution_id).state == "cancel_requested":
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
                    result = RuntimeResult(
                        "cancelled", "stopped", "completed_after_cancel", usage=result.usage
                    )
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
