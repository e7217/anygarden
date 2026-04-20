"""Parse LLM response ``usage`` fields and persist them (#197).

Two upstream shapes are common in 2026:

- **Anthropic** (``/v1/messages``) — ``usage.input_tokens`` /
  ``usage.output_tokens``.
- **OpenAI** (``/v1/chat/completions``) — ``usage.prompt_tokens`` /
  ``usage.completion_tokens``.

Both appear at the root of a non-stream JSON response. For SSE
streams Anthropic emits a terminal ``message_stop`` event whose
``amazon-bedrock-invocationMetrics`` / ``usage`` payload carries the
final counts; OpenAI's stream only reports usage when the caller
sets ``stream_options.include_usage=True``. The parser is defensive
— missing keys map to ``None`` and the row is still recorded (so
"how many requests hit each model" stays accurate even if token
counts are unavailable).

Writes go through a background task queue so the reverse-proxy
response isn't blocked on DB I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ParsedUsage:
    """Normalised view over Anthropic / OpenAI ``usage`` payloads."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


def parse_json_usage(body: dict[str, Any]) -> ParsedUsage:
    """Pull usage counters out of a non-streaming response body.

    Accepts either Anthropic or OpenAI shape. Returns
    ``ParsedUsage()`` (all ``None``) if neither pattern matches — the
    caller still records the request, just without token counts.
    """
    raise NotImplementedError  # Phase 2 — TDD implementation


def parse_stream_event(event: dict[str, Any]) -> Optional[ParsedUsage]:
    """Pull usage from a single SSE event, or ``None`` if absent.

    Called on every event the reverse proxy relays. Most events return
    ``None``; the terminal one (Anthropic ``message_stop`` /
    OpenAI final chunk with ``usage`` included) returns the parsed
    counters. The reverse proxy keeps the last non-None result.
    """
    raise NotImplementedError
