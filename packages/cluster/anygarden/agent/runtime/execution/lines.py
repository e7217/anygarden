"""Split an engine's JSONL stdout without letting one huge line end the turn.

``StreamReader.readline()`` raises once a line passes the reader limit, and
the runtime then reports the whole turn as unknown (#974). Engines put tool
call arguments on a single event line, so publishing a large document is
enough to cross any fixed limit. A line over ``limit`` is dropped up to its
newline and reading continues; only its size and event type are logged,
never the engine output itself.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import structlog

logger = structlog.get_logger(__name__)

# The reply text is capped at 1 MiB (``MAX_TEXT``); the line limit leaves room
# for the event envelope around it, so a reply or ``turn.completed`` is never
# the line that gets dropped.
MAX_LINE = 4 * 1_048_576
CHUNK = 65_536
_KIND = re.compile(rb'"type"\s*:\s*"([A-Za-z0-9_.\-]{1,64})"')


def _event_kind(head: bytes) -> str | None:
    match = _KIND.search(head[:4096])
    return match.group(1).decode() if match else None


async def engine_lines(
    stream, *, engine: str, limit: int = MAX_LINE
) -> AsyncIterator[bytes | None]:
    """Yield each complete line; ``None`` stands in for a dropped one.

    Callers treat ``None`` like unparseable output (it still counts as
    activity), so timeout classification stays fail-closed.
    """
    buffer = bytearray()
    dropping = False
    head = b""
    dropped = 0
    while True:
        chunk = await stream.read(CHUNK)
        if not chunk:
            break
        start = 0
        while True:
            newline = chunk.find(b"\n", start)
            piece = chunk[start:] if newline < 0 else chunk[start:newline + 1]
            if dropping:
                dropped += len(piece)
            else:
                buffer += piece
                if len(buffer) > limit:
                    dropping = True
                    head = bytes(buffer[:4096])
                    dropped = len(buffer)
                    buffer.clear()
            if newline < 0:
                break
            start = newline + 1
            if dropping:
                logger.warning(
                    "engine_output.line_dropped",
                    engine=engine, size=dropped, limit=limit,
                    event_type=_event_kind(head),
                )
                dropping = False
                dropped = 0
                yield None
            else:
                line = bytes(buffer)
                buffer.clear()
                yield line
    if dropping:
        logger.warning(
            "engine_output.line_dropped",
            engine=engine, size=dropped, limit=limit, event_type=_event_kind(head),
        )
        yield None
    elif buffer:
        yield bytes(buffer)


def log_invalid_output(engine: str, exc: BaseException) -> None:
    """Record why a turn became ``invalid_runtime_output`` (#974).

    Only the exception class and the start of its message; engine output is
    never copied into the log.
    """
    logger.warning(
        "engine_output.invalid",
        engine=engine, error=type(exc).__name__, detail=str(exc)[:200],
    )
