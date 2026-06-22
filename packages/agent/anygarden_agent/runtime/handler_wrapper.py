"""Per-room handler supervisor.

Serializes handler invocations for a given room — exactly one turn runs
at a time. A follow-up that arrives while a turn is in flight is no
longer dropped: it is appended to a small bounded per-room FIFO queue
(#457 Wave 2b) and run in arrival order after the in-flight turn (and any
already-queued items) finish, with the lock held throughout the drain so
two turns can never interleave. Only when the queue is at its cap does a
follow-up fall back to the legacy ``rejected`` drop + user notice. Items
that sit past a TTL are skipped on drain (``stale``) so a late answer
isn't posted long after the user moved on.

It emits the four lifecycle events that the cluster persists for
end-to-end request tracing:

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
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque, Optional, Tuple, Union

from anygarden_agent.observability import metrics as _metrics


_ERROR_MAX_CHARS = 500


def _truncate(s: str, limit: int = _ERROR_MAX_CHARS) -> str:
    """Bound an error string so a multi-MB stacktrace can't bloat the log."""
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def is_transient_error(detail: str) -> bool:
    """Best-effort: does ``detail`` describe a clearly-transient failure?

    #457 — used by engine adapters to tag an :class:`EngineError` with
    ``transient=True`` so the opt-in retry path (default OFF) may re-run an
    *empty* turn. Intentionally minimal and conservative — only obvious
    rate-limit / upstream-5xx / connection-reset signals match. A miss
    just means "no retry", which is the safe default; a false positive
    only matters when an operator has opted in (``ANYGARDEN_TURN_MAX_RETRY_
    ATTEMPTS > 0``) and is still guarded by the empty-output check.

    Matches (case-insensitive substrings / status codes):
      - HTTP 429 (rate limit), 500/502/503/504 (upstream/gateway)
      - "rate limit", "too many requests", "overloaded"
      - connection reset/refused/aborted, "connection error",
        "timed out" / "timeout" at the transport layer
    """
    if not detail:
        return False
    text = detail.lower()
    # Transient HTTP status codes (word-bounded-ish to avoid matching e.g.
    # a port number 5000 — require the code as a standalone token).
    for code in ("429", "500", "502", "503", "504"):
        if code in _status_tokens(text):
            return True
    needles = (
        "rate limit",
        "too many requests",
        "overloaded",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "connection reset",
        "connection refused",
        "connection aborted",
        "connection error",
        "connect error",
        "connecttimeout",
        "read timeout",
        "timed out",
        "temporarily unavailable",
    )
    return any(n in text for n in needles)


def _status_tokens(text: str) -> set[str]:
    """Standalone 3-digit numeric tokens in ``text`` (for HTTP-code match)."""
    import re

    return set(re.findall(r"\b\d{3}\b", text))


def _env_int(name: str, default: int) -> int:
    """Read a non-negative int env override, falling back on garbage input."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val >= 0 else default


# ── #457 Wave 2b tunables ────────────────────────────────────────────
# Bounded per-room follow-up queue. Small cap + conservative TTL keeps a
# burst of quick follow-ups (DM rapid-fire, [HANDOFF]) flowing without
# unbounded memory growth or stale (late) replies. Over-cap still drops
# with the legacy ``rejected`` notice.
_MAX_QUEUE_DEPTH = _env_int("ANYGARDEN_ROOM_QUEUE_DEPTH", 3)
_QUEUE_ITEM_TTL_SEC = float(_env_int("ANYGARDEN_ROOM_QUEUE_TTL_SEC", 60))

# Transient retry, default OFF (0 attempts → no behaviour change on merge).
# Only an empty failed/timeout turn whose cause is a transient EngineError
# is retried, and only when no output was produced (no double-posting).
_MAX_RETRY_ATTEMPTS = _env_int("ANYGARDEN_TURN_MAX_RETRY_ATTEMPTS", 0)
_RETRY_BACKOFF_BASE_SEC = 2.0
_RETRY_BACKOFF_CAP_SEC = 8.0


class EngineError(Exception):
    """A turn failed inside an engine adapter (#422).

    Adapters used to swallow turn failures (``except: return None``),
    which the supervisor recorded as ``outcome=ok`` with an empty
    response — a silent response loss the #420 design set out to
    eliminate. Adapters now ``raise EngineError`` instead so the
    supervisor surfaces ``outcome=failed`` and notifies the user.

    #457 — ``transient`` flags a clearly-recoverable failure (429/5xx,
    connection reset/timeout) so the opt-in retry path (default OFF) can
    re-run an *empty* turn. Stays a plain ``Exception`` subclass; the
    flag is a keyword-only init arg so existing ``EngineError("msg")``
    call sites are unchanged and default to ``transient=False``.
    """

    def __init__(self, *args: Any, transient: bool = False) -> None:
        super().__init__(*args)
        self.transient = transient


class EngineTimeoutError(EngineError):
    """An adapter-level turn timeout (e.g. codex ``_CODEX_TURN_TIMEOUT``).

    Mapped to ``outcome=timeout`` rather than ``failed`` so adapter
    timeouts are indistinguishable from the supervisor's own
    ``wait_for`` timeout in the event log.
    """


@dataclass
class EngineTurn:
    """Richer engine result (#433, #461) — gateway-free LLM turn I/O + usage.

    A ``run_engine`` callback may return this instead of a bare ``str``
    to also surface the augmented input the adapter handed the engine
    (``prompt``) alongside the reply (``response``). The supervisor puts
    both on the ``engine_call_finished`` frame so the cluster can stamp
    them onto the ``agent.engine_call`` span — no LLM gateway needed.

    #461 (Wave 2d) — CLI engines (claude-code / codex / gemini) bypass
    the LLM gateway, so their token usage never reached the central
    ``LLMGatewayUsage`` telemetry. An adapter that can read its engine
    SDK's usage now reports it here: ``model`` (resolved model name),
    ``input_tokens`` / ``output_tokens`` (prompt / completion counts),
    and ``cost_usd`` (provider/SDK self-reported cost when available —
    claude-code's ``ResultMessage.total_cost_usd``). The supervisor
    forwards these four on the ``engine_call_finished`` frame and the
    cluster persists one usage row from them. All default ``None`` so a
    bare ``str`` return — or an adapter that can't surface usage — is
    unaffected and writes no token-bearing row (openhands, which routes
    through the gateway reverse-proxy, deliberately leaves them ``None``
    to avoid double-counting).

    A plain ``str`` return stays valid (every field simply omitted), so
    adapters/tests that don't opt in are unaffected.
    """

    response: Optional[str]
    prompt: Optional[str] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None


# A run_engine callback may return the bare reply (legacy) or an
# EngineTurn carrying the turn input too (#433).
EngineResult = Union[str, EngineTurn, None]


def _normalize_engine_result(raw: EngineResult) -> tuple[Optional[str], Optional[str]]:
    """Split a run_engine result into ``(response, prompt)``.

    ``str``/``None`` → ``(raw, None)`` (no turn input captured);
    ``EngineTurn`` → ``(response, prompt)``.
    """
    if isinstance(raw, EngineTurn):
        return raw.response, raw.prompt
    return raw, None


# #457 — a deferred follow-up: the request_id, its run_engine closure, and
# the monotonic timestamp it was enqueued at (for TTL skip on drain).
_QueueItem = Tuple[Optional[str], Callable[[], Awaitable["EngineResult"]], float]


# User-facing notices. Kept short and tagged so the cluster can render
# them as system messages without leaking internal error detail.
_TIMEOUT_NOTICE = "⚠️ 응답이 타임아웃으로 중단되었습니다."
_FAILED_NOTICE = "⚠️ 에이전트가 응답을 생성하지 못했습니다."
_REJECTED_NOTICE = "⚠️ 에이전트가 다른 요청을 처리 중이라 이 메시지를 받지 못했습니다."
# #457 — a queued follow-up sat past its TTL before the queue drained;
# answering it now would be a stale reply, so it is skipped with a notice
# rather than silently dropped.
_STALE_NOTICE = "⚠️ 대기 중이던 메시지가 너무 오래되어 처리하지 않았습니다."


class RoomHandlerSupervisor:
    """Serialize handler invocations per-room and emit lifecycle events."""

    def __init__(self, client: Any, engine_name: str, engine_timeout: float) -> None:
        self._client = client
        self._engine = engine_name
        self._timeout = engine_timeout
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, Optional[str]] = {}
        # #457 — bounded per-room FIFO of deferred follow-ups. Touched only
        # by the lock holder (drain) and by ``dispatch`` callers that found
        # the lock held; both run on the single event loop so no extra
        # synchronisation is needed beyond the room lock for execution.
        self._queues: dict[str, Deque[_QueueItem]] = {}

    async def dispatch(
        self,
        room_id: str,
        request_id: Optional[str],
        run_engine: Callable[[], Awaitable[EngineResult]],
    ) -> None:
        lock = self._room_locks.setdefault(room_id, asyncio.Lock())
        if lock.locked():
            # A turn is in flight (or the holder is mid-drain). Defer this
            # follow-up onto the bounded queue instead of dropping it; the
            # current lock holder will run it FIFO after it finishes. Only
            # an at-cap queue falls back to the legacy ``rejected`` drop.
            queue = self._queues.setdefault(room_id, deque())
            if len(queue) < _MAX_QUEUE_DEPTH:
                queue.append((request_id, run_engine, time.monotonic()))
                # ``queued`` is a terminal handler_finished result, not a
                # new lifecycle phase — no user notice (the turn will be
                # answered for real once it drains).
                existing = self._inflight.get(room_id)
                await self._client.sendLifecycle(
                    room_id,
                    request_id,
                    event="handler_finished",
                    outcome="queued",
                    error=f"deferred behind request_id={existing}",
                )
                return
            # Queue is at cap — preserve the Wave 0 behaviour: reject + notice.
            existing = self._inflight.get(room_id)
            await self._client.sendLifecycle(
                room_id,
                request_id,
                event="handler_finished",
                outcome="rejected",
                error=f"room busy with request_id={existing}",
            )
            # Symmetric with the timeout/failed paths: notify the user that
            # their message was dropped instead of leaving them in silence.
            await self._client.send(
                room_id,
                _REJECTED_NOTICE,
                metadata={"request_id": request_id} if request_id else None,
            )
            return
        async with lock:
            self._inflight[room_id] = request_id
            try:
                await self._run(room_id, request_id, run_engine)
                # Drain any follow-ups that arrived while this turn ran. The
                # lock is still held for the whole drain, so a new dispatch
                # racing in keeps seeing ``lock.locked()`` and enqueues —
                # exactly one turn ever executes per room (serialization
                # invariant). Each queued item runs through the same
                # ``_run`` so it gets the full lifecycle + retry treatment.
                await self._drain_queue(room_id)
            finally:
                self._inflight.pop(room_id, None)

    async def _drain_queue(self, room_id: str) -> None:
        """Run queued follow-ups FIFO while still holding the room lock.

        Called by the lock holder after its own ``_run`` returns. Items
        older than ``_QUEUE_ITEM_TTL_SEC`` are skipped (``stale``) so a
        late reply isn't posted long after the user moved on. New
        follow-ups arriving mid-drain are appended to the same deque and
        picked up in this loop, so a steady stream is served in order
        without ever releasing the lock between items.
        """
        queue = self._queues.get(room_id)
        if queue is None:
            return
        while queue:
            req_id, run_engine, enqueued_at = queue.popleft()
            if (time.monotonic() - enqueued_at) > _QUEUE_ITEM_TTL_SEC:
                # Stale — skip rather than answer late. Mirror the rejected
                # shape: a terminal handler_finished + a user notice.
                await self._client.sendLifecycle(
                    room_id,
                    req_id,
                    event="handler_finished",
                    outcome="rejected",
                    error="queued turn skipped: exceeded TTL",
                )
                await self._client.send(
                    room_id,
                    _STALE_NOTICE,
                    metadata={"request_id": req_id} if req_id else None,
                )
                continue
            self._inflight[room_id] = req_id
            await self._run(room_id, req_id, run_engine)
        # Tidy up the empty deque so idle rooms don't accumulate state.
        if not queue:
            self._queues.pop(room_id, None)

    async def _run(
        self,
        room_id: str,
        request_id: Optional[str],
        run_engine: Callable[[], Awaitable[EngineResult]],
    ) -> None:
        started = time.monotonic()
        await self._client.sendLifecycle(
            room_id, request_id, event="handler_started"
        )

        # #457 — opt-in transient retry (default OFF). The handler_started
        # above fires once; each attempt emits its own engine_call_started/
        # finished. A retriable empty failure re-runs the engine after a
        # bounded backoff; the terminal handler_finished carries ``ok`` on
        # eventual success or ``retry_exhausted`` once attempts run out.
        attempt = 0
        retried = False
        while True:
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
            prompt: Optional[str] = None  # #433 — augmented turn input, if any
            # #461 — gateway-free LLM usage carried off an EngineTurn.
            model: Optional[str] = None
            input_tokens: Optional[int] = None
            output_tokens: Optional[int] = None
            cost_usd: Optional[float] = None
            transient = False  # #457 — set from a transient EngineError cause
            # #433 — turn I/O capture is opt-in: only when the adapter returns
            # an EngineTurn (not a bare str) do we surface prompt/completion.
            # Keeps the feature a single predictable toggle rather than
            # half-capturing output for un-migrated adapters.
            io_capture = False
            try:
                raw = await asyncio.wait_for(
                    run_engine(), timeout=self._timeout
                )
                response, prompt = _normalize_engine_result(raw)
                io_capture = isinstance(raw, EngineTurn)
                if io_capture:
                    # #461 — usage telemetry rides the same EngineTurn opt-in.
                    model = raw.model
                    input_tokens = raw.input_tokens
                    output_tokens = raw.output_tokens
                    cost_usd = raw.cost_usd
            except asyncio.TimeoutError:
                outcome = "timeout"
                error = f"engine exceeded {self._timeout}s"
            except EngineTimeoutError as exc:
                # #422 — adapter-level timeout (e.g. codex turn timeout).
                outcome = "timeout"
                error = _truncate(str(exc))
                transient = bool(getattr(exc, "transient", False))
            except asyncio.CancelledError:
                # User cancellation — never retried/queued. Close the spans
                # and re-raise immediately.
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
            except EngineError as exc:
                # #422/#457 — classified adapter failure; ``transient``
                # gates the opt-in retry below.
                outcome = "failed"
                error = _truncate(str(exc))
                transient = bool(getattr(exc, "transient", False))
            except Exception as exc:  # noqa: BLE001 — best-effort error capture
                outcome = "failed"
                error = _truncate(str(exc))

            # #422 — a tracked (user-triggered) turn that produced no text is
            # a silent failure, not a legitimate no-reply. Ambient no-reply
            # flows through ``ingest_context`` (decide_policy → INGEST_ONLY)
            # and never reaches the supervisor, so an empty result on a turn
            # that carries a ``request_id`` means the engine was asked to
            # answer and didn't. Surface it as ``failed`` + a user notice
            # rather than leaving the user staring at silence.
            if outcome == "ok" and not response and request_id is not None:
                outcome = "failed"
                if error is None:
                    error = "engine produced no response"
            elif outcome == "ok" and not response and request_id is None:
                # #482 — an *untracked* empty turn (request_id is None,
                # e.g. a non-nominated peer mention) is a legitimate
                # no-reply, so it must stay ``outcome=ok`` with no room
                # send — reclassifying it to ``failed`` would spam the
                # room with false failures for the intended silence.
                # But it used to be wholly invisible. Tag the engine
                # frame with a sentinel ``error`` (outcome unchanged) so
                # the cluster persists it as a queryable detail, and bump
                # the in-process counter so the no-response rate is
                # measurable. Room behaviour is unchanged.
                if error is None:
                    error = "no_response(untracked)"
                _metrics.agent_empty_untracked_total.inc()

            engine_dur = int((time.monotonic() - engine_started) * 1000)
            await self._client.sendLifecycle(
                room_id,
                request_id,
                event="engine_call_finished",
                outcome=outcome,
                duration_ms=engine_dur,
                engine=self._engine,
                error=error,
                # #433 — gateway-free turn I/O, emitted only on an EngineTurn
                # opt-in. ``completion`` is the reply (None on empty/failed/
                # timeout). Both wire-excluded when None.
                prompt=prompt if io_capture else None,
                completion=(response if response else None) if io_capture else None,
                # #461 — gateway-free LLM usage telemetry. Token counts are
                # non-sensitive (needed for billing/observability) and are
                # carried whenever the adapter reported them, regardless of
                # the content-capture gate (which only governs prompt/
                # completion TEXT). These stay None for a bare-str return or
                # an adapter that can't surface usage (e.g. openhands, which
                # is already counted via the gateway reverse-proxy), so no
                # frame-sourced usage row is written for those turns.
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )

            # #457 — decide whether to retry this attempt. Retry ONLY when:
            #  - the outcome is a recoverable failure (timeout/failed),
            #  - the cause was flagged transient (429/5xx/conn reset),
            #  - NO output was produced (guard against double-posting a
            #    partial reply), and
            #  - attempts remain. Default ``_MAX_RETRY_ATTEMPTS == 0`` makes
            #    this branch unreachable, so merge is behaviour-neutral.
            should_retry = (
                outcome in ("timeout", "failed")
                and transient
                and not response
                and attempt < _MAX_RETRY_ATTEMPTS
            )
            if should_retry:
                retried = True
                attempt += 1
                # Signal the retry (no user notice — the real answer or the
                # exhaustion notice comes after). Shaped like the other
                # terminal-ish results so trace/metrics see a ``retrying``.
                await self._client.sendLifecycle(
                    room_id,
                    request_id,
                    event="handler_finished",
                    outcome="retrying",
                    error=error,
                )
                backoff = min(
                    _RETRY_BACKOFF_BASE_SEC * (2 ** (attempt - 1)),
                    _RETRY_BACKOFF_CAP_SEC,
                )
                await asyncio.sleep(backoff)
                continue

            # #457 — retries are spent (or none were configured). If we
            # actually retried and still failed empty, the terminal outcome
            # is ``retry_exhausted``; the user still gets the failed notice.
            if retried and outcome in ("timeout", "failed") and not response:
                outcome = "retry_exhausted"
            break

        send_metadata = {"request_id": request_id} if request_id else None
        # ``response`` truthy → deliver it. An empty result reaches here
        # only for proactive/untracked turns (request_id is None); those
        # keep the legitimate "no-reply" semantics. Tracked empty turns
        # were already reclassified to ``failed`` above. ``timeout`` and
        # ``failed`` both notify the user so silence never reads as success.
        if response:
            await self._client.send(room_id, response, metadata=send_metadata)
        elif outcome == "timeout":
            await self._client.send(
                room_id, _TIMEOUT_NOTICE, metadata=send_metadata
            )
        elif outcome in ("failed", "retry_exhausted"):
            # #457 — a spent retry surfaces the same failed notice as a
            # one-shot failure; the distinction lives in the outcome label
            # for tracing/metrics, not in the user-facing text.
            await self._client.send(
                room_id, _FAILED_NOTICE, metadata=send_metadata
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
