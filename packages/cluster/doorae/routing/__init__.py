"""Auto-routing of unassigned tasks via the room representative
agent (#313).

The cluster delegates the routing decision to the rep agent's actual
reasoning chain rather than making its own LLM call. The flow is:

1. ``POST /api/v1/rooms/{id}/auto-route-unassigned`` collects the
   room's unassigned tasks and the candidate agents (with their
   ``description`` fields, #271).
2. ``format_routing_prompt`` produces a deterministic system message
   carrying ``[DOORAE_ROUTING_REQUEST id=<rid>]`` + the JSON payload.
3. The cluster injects this as a synthetic mention to the rep
   agent and registers an ``asyncio.Future`` keyed by ``rid`` in
   ``app.state.routing_futures``.
4. The agent SDK's existing ``decide_policy`` mention path picks
   up the message; the LLM is instructed to answer with
   ``[DOORAE_ROUTING_RESPONSE id=<rid>]\n{json}``.
5. The cluster's WS message handler (``ws/handler.py``) detects
   inbound messages with the response marker, hands the parsed
   JSON to ``Future.set_result``, and tags the message with
   ``metadata.system_origin = 'auto_route_response'`` so the
   frontend can hide it from chat.
6. The endpoint awaits the Future with a 30s timeout and applies
   the assignments via ``inject_task_assignment_message`` so each
   newly-routed task wakes its assignee through the standard #266
   path.

Why a marker-in-content protocol instead of WS frames or MCP tools:
the marker piggybacks on ``decide_policy`` + ``on_message`` /
``assemble_user_content`` which are already shared across all 3
adapters via ``EngineAdapter`` (#293). Zero engine-specific code.

TODO: When the LiteLLM gateway (#197) becomes operationally
reliable, an alternative server-side routing path can register
the same ``app.state.routing_futures`` keyed Future and resolve it
without the WS roundtrip — same API contract, fallback for
rep-offline cases.
"""

from doorae.routing.protocol import (
    ROUTING_REQUEST_MARKER,
    ROUTING_RESPONSE_MARKER,
    RoutingResult,
    format_routing_prompt,
    parse_routing_response,
    try_parse_routing_response,
)

__all__ = [
    "ROUTING_REQUEST_MARKER",
    "ROUTING_RESPONSE_MARKER",
    "RoutingResult",
    "format_routing_prompt",
    "parse_routing_response",
    "try_parse_routing_response",
]
