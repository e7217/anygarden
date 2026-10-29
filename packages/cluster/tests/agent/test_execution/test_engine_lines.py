"""#974 — splitting engine stdout without a fatal per-line limit."""

from __future__ import annotations

import asyncio

import structlog

from anygarden.agent.runtime.execution.lines import engine_lines


async def lines(data: bytes, *, limit: int, chunks: int = 7) -> list[bytes | None]:
    reader = asyncio.StreamReader()
    for start in range(0, len(data), chunks):
        reader.feed_data(data[start:start + chunks])
    reader.feed_eof()
    return [line async for line in engine_lines(reader, engine="test", limit=limit)]


async def test_splits_lines_across_chunk_boundaries():
    assert await lines(b'{"a":1}\n{"b":2}\ntail', limit=64) == [
        b'{"a":1}\n', b'{"b":2}\n', b"tail",
    ]


async def test_oversized_line_is_dropped_and_reading_continues():
    big = b'{"type":"item.started","x":"' + b"y" * 200 + b'"}\n'
    with structlog.testing.capture_logs() as logs:
        out = await lines(b'{"ok":1}\n' + big + b'{"ok":2}\n', limit=64)
    assert out == [b'{"ok":1}\n', None, b'{"ok":2}\n']
    [entry] = logs
    assert entry["event"] == "engine_output.line_dropped"
    assert entry["event_type"] == "item.started" and entry["size"] == len(big)


async def test_unterminated_oversized_tail_is_reported():
    with structlog.testing.capture_logs() as logs:
        out = await lines(b"z" * 100, limit=64)
    assert out == [None]
    assert logs[0]["event_type"] is None and logs[0]["size"] == 100


async def test_line_exactly_at_limit_is_kept():
    line = b"a" * 63 + b"\n"
    assert await lines(line, limit=64) == [line]
