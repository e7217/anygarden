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
    usage = body.get("usage") or {}
    return ParsedUsage(
        # Anthropic uses ``input_tokens`` / ``output_tokens``; OpenAI
        # uses ``prompt_tokens`` / ``completion_tokens``. Check the
        # Anthropic field first since it's unambiguous, then fall back
        # so a response carrying both keys prefers the native one.
        prompt_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
        completion_tokens=(
            usage.get("output_tokens") or usage.get("completion_tokens")
        ),
    )


def parse_stream_event(event: dict[str, Any]) -> Optional[ParsedUsage]:
    """Pull usage from a single SSE event, or ``None`` if absent.

    Called on every event the reverse proxy relays. Most events return
    ``None``; the terminal one (Anthropic ``message_stop`` /
    OpenAI final chunk with ``usage`` included) returns the parsed
    counters. The reverse proxy keeps the last non-None result.
    """
    # Anthropic ``message_start`` carries ``input_tokens`` inside a
    # nested ``message.usage`` object.
    event_type = event.get("type")
    if event_type == "message_start":
        inner = (event.get("message") or {}).get("usage") or {}
        if inner:
            return ParsedUsage(
                prompt_tokens=inner.get("input_tokens"),
                completion_tokens=inner.get("output_tokens") or None,
            )
    # Anthropic ``message_delta`` carries ``output_tokens`` at the
    # event root just before ``message_stop``.
    if event_type == "message_delta":
        usage = event.get("usage") or {}
        if usage:
            return ParsedUsage(
                prompt_tokens=usage.get("input_tokens"),
                completion_tokens=usage.get("output_tokens"),
            )
    # OpenAI's final chunk (when include_usage=True) and any other
    # shape that simply carries a root-level ``usage`` field.
    usage = event.get("usage")
    if isinstance(usage, dict):
        return ParsedUsage(
            prompt_tokens=usage.get("input_tokens") or usage.get("prompt_tokens"),
            completion_tokens=(
                usage.get("output_tokens") or usage.get("completion_tokens")
            ),
        )
    return None
