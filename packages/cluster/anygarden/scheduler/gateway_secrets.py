"""Build per-engine ``engine_secrets`` for the spawn frame (#359).

ADR 004 (``docs/decisions/004-embedded-litellm-gateway.md``) Phase 5
prescribes that when the LLM gateway is enabled, the spawn frame
carries provider ``BASE_URL`` + ``AUTH_TOKEN`` env keys so the agent
SDKs route through the anygarden reverse proxy at
``/api/v1/llm/*`` instead of going to upstream APIs directly.

Issue #359 narrows that to **openhands only** for the first cut. The
key names (``OPENAI_BASE_URL``, ``ANTHROPIC_BASE_URL``, …) are
provider-SDK-wide standards, so populating them universally would
silently re-route the three CLI engines (claude-code, codex,
gemini-cli) through the gateway too. The gateway DB currently only
has Ollama models registered, so claude-code requests would fail with
"model not found" and the user-visible regression is "oh-agent01
starts working but agent01-claude / agent01-codex / agent01-gemini
break". Engine-scoped secrets sidestep that: only openhands sees the
overrides.

Future follow-up (separate issue): once an operator has registered
Anthropic / OpenAI / Google models in the gateway DB and verified the
routing, the engine guard here can be relaxed to extend the
``engine_secrets`` to those engines too.
"""

from __future__ import annotations


__all__ = ["build_engine_secrets", "openhands_model_id_for_gateway"]


def build_engine_secrets(
    *,
    engine: str,
    gateway_enabled: bool,
    cluster_external_url: str | None,
    agent_token: str | None,
) -> dict[str, str]:
    """Return the env keys the agent should inject when calling the LLM.

    Empty dict in any of these cases (the spawn frame then carries an
    empty ``engine_secrets`` and the agent process keeps using whatever
    auth it had before — the same behaviour as pre-#359 anygarden):

    - ``engine != "openhands"`` — engine guard. See module docstring.
    - ``gateway_enabled`` is False — feature flag off, leave the
      gateway out of the loop entirely.
    - ``cluster_external_url`` is missing/empty — without a reachable
      anygarden URL there's nothing to put in ``BASE_URL``, so degrade
      to "no gateway routing" instead of emitting a half-broken pair.
    - ``agent_token`` is missing/empty — the reverse proxy at
      ``/api/v1/llm/*`` requires an authenticated identity; emitting
      ``OPENAI_API_KEY=""`` would fail at the auth middleware before
      ever reaching litellm, so omit the keys entirely and let the
      adapter's own degradation path take over.

    When all four conditions hold, returns the OpenAI-compat env pair
    pointing at the anygarden reverse proxy. The agent token doubles as
    the API key — the proxy validates it via ``get_current_identity``
    (any of user / agent / machine tokens pass) before forwarding to
    the litellm subprocess with the gateway's master key. Phase 0
    scope is OPENAI_* only because the Ollama-only catalog uses
    ``openai/<model>`` prefixes; ANTHROPIC_* / GEMINI_* are deferred
    to a follow-up that has the matching gateway entries.
    """
    if engine != "openhands":
        return {}
    if not gateway_enabled:
        return {}
    if not cluster_external_url:
        return {}
    if not agent_token:
        return {}

    base = cluster_external_url.rstrip("/")
    return {
        # litellm proxy serves OpenAI-compat at ``/v1/...`` under the
        # mount prefix; anygarden reverse-proxies that under
        # ``/api/v1/llm/v1/*`` after auth replacement.
        "OPENAI_BASE_URL": f"{base}/api/v1/llm/v1",
        "OPENAI_API_KEY": agent_token,
    }


def openhands_model_id_for_gateway(provider: str, model_name: str) -> str | None:
    """Turn an ``LLMGatewayModel`` row into the model id OpenHands expects.

    The OpenHands SDK uses litellm under the hood and routes on
    ``provider/model`` prefixes. anygarden's gateway exposes
    OpenAI-compatible ``/v1/chat/completions`` regardless of the
    upstream provider, so the model id we hand to OpenHands always
    starts with ``openai/`` — what differs is the rest:

    - ``provider="ollama"`` → ``openai/<model_name>``. The litellm
      proxy intercepts ``openai/<id>`` requests and maps them via the
      DB's ``upstream_model`` (``ollama/<id>``) to the actual ollama
      backend. OpenHands never sees ollama directly.
    - ``provider in {"openai", "anthropic", "gemini"}`` → same
      pattern; the proxy handles upstream routing.
    - ``provider="bedrock" / "vertex" / "azure" / "custom"`` →
      same. The proxy abstracts the upstream surface entirely.

    All paths return ``openai/<model_name>`` because the proxy is
    always OpenAI-shaped to the agent. This collapses the apparent
    provider-prefix branching into a single id format. Returns
    ``None`` only when ``model_name`` is empty (defensive — caller
    should never pass that, but cheap to guard).
    """
    if not model_name:
        return None
    return f"openai/{model_name}"
