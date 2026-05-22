"""Per-room handler supervisor.

Serializes handler invocations for a given room (no queuing — a
second concurrent dispatch is rejected with an explicit lifecycle
event) and emits the four lifecycle events that the cluster
persists for end-to-end request tracing:

    handler_started
    engine_call_started
    engine_call_finished  (with outcome: ok | failed | timeout | cancelled)
    handler_finished      (with the terminal outcome)

Every engine call is wrapped in ``asyncio.wait_for(..., engine_timeout)``
so a stuck subprocess surfaces as ``outcome=timeout`` rather than
leaving the handler hung forever (the failure mode that swamped
the production agent with typing pings in #204).

Design reference: docs/plans/2026-04-20-agent-observability-design.md
§4 "Agent-side concurrency and timeout".
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional


_ERROR_MAX_CHARS = 500


def _truncate(s: str, limit: int = _ERROR_MAX_CHARS) -> str:
    """Bound an error string so a multi-MB stacktrace can't bloat the log."""
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


class RoomHandlerSupervisor:
    """Serialize handler invocations per-room and emit lifecycle events."""

    def __init__(self, client: Any, engine_name: str, engine_timeout: float) -> None:
        self._client = client
        self._engine = engine_name
        self._timeout = engine_timeout
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, Optional[str]] = {}

    async def dispatch(
        self,
        room_id: str,
        request_id: Optional[str],
        run_engine: Callable[[], Awaitable[str]],
    ) -> None:
        lock = self._room_locks.setdefault(room_id, asyncio.Lock())
        if lock.locked():
            existing = self._inflight.get(room_id)
            await self._client.sendLifecycle(
                room_id,
                request_id,
                event="handler_finished",
                outcome="rejected",
                error=f"room busy with request_id={existing}",
            )
            return
        async with lock:
            self._inflight[room_id] = request_id
            try:
                await self._run(room_id, request_id, run_engine)
            finally:
                self._inflight.pop(room_id, None)

    async def _run(
        self,
        room_id: str,
        request_id: Optional[str],
        run_engine: Callable[[], Awaitable[str]],
    ) -> None:
        started = time.monotonic()
        await self._client.sendLifecycle(
            room_id, request_id, event="handler_started"
        )

        engine_started = time.monotonic()
        await self._client.sendLifecycle(
            room_id,
            request_id,
            event="engine_call_started",
            engine=self._engine,
        )

        outcome: str = "ok"
        error: Optional[str] = None
        response: Optional[str] = None
        try:
            response = await asyncio.wait_for(
                run_engine(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            outcome = "timeout"
            error = f"engine exceeded {self._timeout}s"
        except asyncio.CancelledError:
            outcome = "cancelled"
            engine_dur = int((time.monotonic() - engine_started) * 1000)
            await self._client.sendLifecycle(
                room_id,
                request_id,
                event="engine_call_finished",
                outcome=outcome,
                duration_ms=engine_dur,
                engine=self._engine,
            )
            total = int((time.monotonic() - started) * 1000)
            await self._client.sendLifecycle(
                room_id,
                request_id,
                event="handler_finished",
                outcome=outcome,
                duration_ms=total,
            )
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort error capture
            outcome = "failed"
            error = _truncate(str(exc))

        engine_dur = int((time.monotonic() - engine_started) * 1000)
        await self._client.sendLifecycle(
            room_id,
            request_id,
            event="engine_call_finished",
            outcome=outcome,
            duration_ms=engine_dur,
            engine=self._engine,
            error=error,
        )

        send_metadata = {"request_id": request_id} if request_id else None
        # Truthiness check: an empty string is a legitimate
        # "no-reply" signal from the engine (e.g. ambient ingestion
        # paths where the adapter deliberately returns "").
        if response:
            await self._client.send(room_id, response, metadata=send_metadata)
        elif outcome == "timeout":
            await self._client.send(
                room_id,
                "⚠️ 응답이 타임아웃으로 중단되었습니다.",
                metadata=send_metadata,
            )

        total = int((time.monotonic() - started) * 1000)
        await self._client.sendLifecycle(
            room_id,
            request_id,
            event="handler_finished",
            outcome=outcome,
            duration_ms=total,
            error=error,
        )
