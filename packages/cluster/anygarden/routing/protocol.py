"""Pure helpers for the rep-routing protocol (#313).

Two functions, both deterministic and DB-free so the matrix can be
tested exhaustively in isolation:

- ``format_routing_prompt(...)`` builds the message body the cluster
  injects into the room. The body is markdown the LLM reads — the
  marker line up top, the agent roster + task list, and the format
  spec at the bottom. Tested by string-equality on a frozen sample.
- ``parse_routing_response(...)`` extracts the JSON mapping from a
  rep-emitted message. LLMs sometimes wrap output in code fences,
  prepend explanations, or skip whitespace; the parser is permissive
  on framing but strict on payload (must be a JSON object whose
  values are strings). Returns ``RoutingResult.unparseable`` rather
  than raising so the API endpoint can fall back without exception
  noise.

Keeping the protocol logic here (not inside the adapter or the API
router) makes it possible for future server-side routers (e.g. a
LiteLLM-gateway-backed fallback when rep is offline) to share the
prompt/parse contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


# Markers are deliberately verbose so an accidental user message
# can't trip the parser. The ``id=`` field correlates request and
# response within a single ``app.state.routing_futures`` registry.
ROUTING_REQUEST_MARKER = "[ANYGARDEN_ROUTING_REQUEST"
ROUTING_RESPONSE_MARKER = "[ANYGARDEN_ROUTING_RESPONSE"


@dataclass(frozen=True)
class _AgentLine:
    """Minimal shape needed for the prompt; callers strip down full
    Agent rows to this so ``format_routing_prompt`` has no SQLAlchemy
    awareness."""

    agent_id: str
    name: str
    description: str | None


@dataclass(frozen=True)
class _TaskLine:
    task_id: str
    title: str


def format_routing_prompt(
    *,
    request_id: str,
    room_name: str,
    agents: list[_AgentLine],
    tasks: list[_TaskLine],
) -> str:
    """Render the body the cluster injects into the room.

    The LLM only reads the prompt; it never sees the request_id used
    by the Future registry. We embed the id in the marker line so the
    response can be matched even if the LLM hallucinates extra prose
    around the JSON.
    """
    agent_lines = []
    for a in agents:
        desc = (a.description or "").strip().replace("\n", " ")
        if desc:
            agent_lines.append(f"- {a.name} ({a.agent_id}): {desc}")
        else:
            agent_lines.append(f"- {a.name} ({a.agent_id})")
    task_lines = [f'- {t.task_id}: "{t.title}"' for t in tasks]

    return (
        f"{ROUTING_REQUEST_MARKER} id={request_id}]\n"
        f"You are the representative agent of room \"{room_name}\".\n"
        "Pick the best agent in the room for each unassigned task,\n"
        "based on the agents' self-descriptions. You may choose\n"
        "yourself if your skills fit.\n"
        "\n"
        "## Agents in this room\n"
        + "\n".join(agent_lines)
        + "\n\n## Unassigned tasks\n"
        + "\n".join(task_lines)
        + "\n\n## Reply format\n"
        f"Reply with ONLY two lines, no extra prose:\n"
        f"```\n"
        f"{ROUTING_RESPONSE_MARKER} id={request_id}]\n"
        '{"<task_id>": "<agent_id>", ...}\n'
        f"```\n"
    )


@dataclass(frozen=True)
class RoutingResult:
    """Outcome of ``parse_routing_response``.

    ``mapping`` is the parsed ``{task_id: agent_id}`` dict on success.
    ``error`` is a short human-readable diagnostic on failure (used
    by the API to report partial outcomes).
    """

    mapping: dict[str, str] | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.mapping is not None

    @classmethod
    def success(cls, mapping: dict[str, str]) -> "RoutingResult":
        return cls(mapping=mapping, error=None)

    @classmethod
    def fail(cls, error: str) -> "RoutingResult":
        return cls(mapping=None, error=error)


# Match the marker line + everything that follows. The trailing
# group is greedy because the JSON object is the last thing the LLM
# is supposed to emit — we extract it from the tail and hand it to
# the JSON parser, which complains loudly if the LLM appended prose.
_RESPONSE_RE = re.compile(
    r"\[ANYGARDEN_ROUTING_RESPONSE\s+id=([^\]]+)\]\s*(.+)",
    re.DOTALL,
)
# A JSON object can hide inside a markdown code fence; this trims
# leading ``` / ```json and trailing ``` so the bare object survives.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_routing_response(
    request_id: str, content: str
) -> RoutingResult:
    """Extract ``{task_id: agent_id}`` from a rep-emitted message.

    Robustness checklist (tested):
    - ``[...] id=<rid>`` matches our request id (rejects mismatches
      so a stale response from a previous request can't poison the
      newest one)
    - JSON inside a markdown ``json fence`` is unwrapped
    - leading prose before the marker line is ignored (the marker
      anchors extraction)
    - values must be strings — rejects ``{t1: 42}`` or arrays
    """
    if ROUTING_RESPONSE_MARKER not in content:
        return RoutingResult.fail("response marker not found")

    match = _RESPONSE_RE.search(content)
    if match is None:
        return RoutingResult.fail("response marker malformed")

    response_id, payload = match.group(1).strip(), match.group(2).strip()
    if response_id != request_id:
        return RoutingResult.fail(
            f"response id {response_id!r} does not match {request_id!r}"
        )

    fence_match = _FENCE_RE.match(payload)
    if fence_match:
        payload = fence_match.group(1).strip()

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        return RoutingResult.fail(f"invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return RoutingResult.fail("payload is not a JSON object")

    mapping: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(k, str) or not isinstance(v, str):
            return RoutingResult.fail(
                f"non-string entry in mapping: {k!r} -> {v!r}"
            )
        mapping[k] = v
    return RoutingResult.success(mapping)


def try_parse_routing_response(
    content: str,
) -> tuple[str, RoutingResult] | None:
    """Best-effort extraction without prior knowledge of request_id.

    Used by the WS message hook — it scans every inbound message
    and only commits when the marker is present. Returns
    ``(request_id, result)`` so the caller can look up the matching
    Future. ``None`` means "not a routing response, ignore".
    """
    if ROUTING_RESPONSE_MARKER not in content:
        return None
    match = _RESPONSE_RE.search(content)
    if match is None:
        return None
    request_id = match.group(1).strip()
    return request_id, parse_routing_response(request_id, content)
