"""Embedded LiteLLM gateway (#197).

anygarden-server supervises a ``litellm`` subprocess listening on
``127.0.0.1:<llm_gateway_port>`` and exposes ``/api/v1/llm/*`` as the
only external access path. See ``docs/design/12-llm-gateway.md`` and
ADR-004 for the architecture and rationale.

Module layout:

- :mod:`.supervisor` — subprocess lifecycle (state machine, crash
  detection + backoff respawn, graceful shutdown, health check).
- :mod:`.config_writer` — DB → ``litellm.yaml`` renderer. Secrets
  never land in the file; only ``os.environ/ANYGARDEN_LITELLM_<KEY>``
  references. Actual values are injected at spawn time.
- :mod:`.reverse_proxy` — FastAPI router mounted at ``/api/v1/llm/*``.
  Swaps the caller's anygarden token for the LiteLLM master key and
  streams the response through.
- :mod:`.usage_logger` — parses Anthropic / OpenAI ``usage`` fields
  from response bodies and records one row per request in
  ``llm_gateway_usage`` via a background task queue.

Feature flag: ``ANYGARDEN_LLM_GATEWAY_ENABLED`` (default ``False``). When
off, none of these modules are instantiated and the existing direct
upstream path stays the only route.
"""

from __future__ import annotations

__all__ = [
    "GatewayState",
    "LLMGatewaySupervisor",
    "render_config",
]

# Re-exports — keeps ``from anygarden.llm_gateway import ...`` ergonomic
# without callers having to know the internal module split. Each
# sub-module is still importable directly for testing.
from anygarden.llm_gateway.supervisor import GatewayState, LLMGatewaySupervisor
from anygarden.llm_gateway.config_writer import render_config
