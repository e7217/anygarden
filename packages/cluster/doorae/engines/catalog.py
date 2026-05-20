"""Static catalog of engine models and reasoning levels.

Last refreshed: 2026-04-25 against the locally-installed CLIs
(``claude`` 2.1.116, ``codex`` 0.121.0, ``gemini`` 0.37.1). Values were
verified by probing ``--help``, triggering validation errors, and
reading the shipped binaries' model-name tables — not by trusting the
vendor marketing docs, which lag behind the actual CLIs.

Exception: ``gpt-5.5`` (codex) was added on the 2026-04-25
announcement and is not yet round-tripped against a live ``codex exec``
call. If ChatGPT-account auth rejects it at runtime, roll the
``default_model`` back to ``gpt-5.4`` — Codex does no client-side
validation, so the catalog can advertise an ID the backend then refuses.

This is intentionally a hand-maintained dict rather than a live query
against each SDK/CLI. Reasons:

- Several engines (Codex CLI, Claude Code SDK) do not expose a clean
  "list models" API that works without a valid API key. Walking an HTTP
  endpoint per admin page load is a usability regression compared to a
  static dict.
- Reasoning-effort taxonomies differ per engine *at the CLI layer*:
  Codex allows ``none/minimal/low/medium/high/xhigh``; Claude Code uses
  ``low/medium/high/xhigh/max`` (``--effort`` flag); Gemini CLI has no
  effort flag — the adapter translates ``low/medium/high`` into the
  ``--thinking-budget`` integer instead. The catalog encodes what the
  UI should show per engine.
- A stale entry here is a trivial PR, not a runtime failure — the
  agent spawn still works because the adapter falls back to its
  built-in default when the requested model is unknown. Codex in
  particular does no client-side model-id validation at all.

Follow-up work (issue #4) may add a dynamic refresh endpoint that
queries the Anthropic/OpenAI/Google SDKs for live model lists and
merges them with this static baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EngineModel:
    """A single model offered by an engine."""

    id: str
    """Identifier passed to the adapter (e.g. ``"gpt-5.4"``)."""

    label: str
    """Human-friendly display name."""

    reasoning_levels: tuple[str, ...] = ()
    """Reasoning levels this specific model supports. Falls back to the
    engine-level ``reasoning_levels`` when empty."""


@dataclass(frozen=True)
class EngineCatalogEntry:
    """Catalog entry for one engine (codex, claude-code, etc.)."""

    engine: str
    default_model: str
    models: tuple[EngineModel, ...]
    reasoning_levels: tuple[str, ...]
    """Engine-level default reasoning levels. Individual models may
    narrow this via their own ``reasoning_levels``."""

    deprecated: bool = False
    """Issue #355 Phase 6 — flag the engine as legacy.

    ``True`` means the admin UI should sort the engine after
    non-deprecated alternatives, badge it as 'legacy', and the API
    should expose the flag so frontends can render guidance toward
    the recommended replacement (typically ``openhands``). The flag
    does **not** disable the engine — agents already pinned to it
    keep running. Actual removal lives in a separate issue tracked
    after Phase 5 validation in ``docs/decisions/005-openhands-
    validation-plan.md`` clears the four decision criteria.

    All existing CLI engines default to ``False`` until those
    criteria pass — flipping prematurely would force a UX
    transition before the empirical case for migration is on
    record.
    """

    deprecation_note: Optional[str] = None
    """Human-readable rationale shown alongside the legacy badge.

    Pre-#355 entries leave this ``None``. When ``deprecated=True``
    is set later, a one-line note here explains the recommended
    replacement and links to the validation results so the admin
    UI can render context, not just a flag."""


ENGINE_CATALOG: dict[str, EngineCatalogEntry] = {
    # Codex CLI: reasoning levels verified by triggering its config
    # validator (it reports ``none/minimal/low/medium/high/xhigh``).
    # Model list verified by round-tripping an actual ``codex exec``
    # call with a ChatGPT-account login. The CLI binary's hardcoded
    # symbol table is *not* authoritative — Codex does no client-side
    # model-id validation, so the source of truth is what the backend
    # accepts.
    #
    # Models confirmed to work under ChatGPT-account auth. Other IDs
    # surfaced in the binary (``gpt-5.4-pro``, ``gpt-5.2-codex``,
    # ``gpt-5.1-codex-max``, ``gpt-5.1-codex-mini``) return
    # "not supported when using Codex with a ChatGPT account" and are
    # presumed API-key-only, so they're omitted until we wire API-key
    # auth through a separate engine entry.
    "codex": EngineCatalogEntry(
        engine="codex",
        default_model="gpt-5.5",
        models=(
            # gpt-5.5: announcement-only on 2026-04-25; runtime
            # round-trip verification pending. See module docstring.
            EngineModel(
                id="gpt-5.5",
                label="GPT-5.5",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
            ),
            EngineModel(
                id="gpt-5.4",
                label="GPT-5.4",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
            ),
            EngineModel(
                id="gpt-5.4-mini",
                label="GPT-5.4 Mini",
                reasoning_levels=("minimal", "low", "medium", "high"),
            ),
            EngineModel(
                id="gpt-5.3-codex",
                label="GPT-5.3 Codex",
                reasoning_levels=("low", "medium", "high", "xhigh"),
            ),
            EngineModel(
                id="gpt-5.3-codex-spark",
                label="GPT-5.3 Codex Spark",
                reasoning_levels=("minimal", "low"),
            ),
            EngineModel(
                id="gpt-5.2",
                label="GPT-5.2",
                reasoning_levels=("low", "medium", "high"),
            ),
        ),
        reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
    ),
    # Claude Code: ``--effort`` (session flag) accepts
    # ``low/medium/high/xhigh/max``. There is no ``disabled`` option at
    # the CLI — the API-level ``extended_thinking`` / ``adaptive``
    # abstractions are hidden behind the single effort knob. Model IDs
    # come from the shipped binary's symbol table (v2.1.116).
    "claude-code": EngineCatalogEntry(
        engine="claude-code",
        default_model="claude-opus-4-7",
        models=(
            EngineModel(
                id="claude-opus-4-7",
                label="Claude Opus 4.7",
            ),
            EngineModel(
                id="claude-opus-4-6",
                label="Claude Opus 4.6",
            ),
            EngineModel(
                id="claude-sonnet-4-6",
                label="Claude Sonnet 4.6",
            ),
            EngineModel(
                id="claude-sonnet-4-5",
                label="Claude Sonnet 4.5",
            ),
            EngineModel(
                id="claude-haiku-4-5",
                label="Claude Haiku 4.5",
            ),
        ),
        reasoning_levels=("low", "medium", "high", "xhigh", "max"),
        deprecated=True,
        deprecation_note="Legacy CLI engine; prefer OpenHands for new agents.",
    ),
    # Gemini CLI: no reasoning-effort flag at the CLI layer. The
    # Doorae adapter (``packages/agent/doorae_agent/integrations/
    # gemini_cli.py``) maps ``low/medium/high`` to
    # ``--thinking-budget 1024/8192/32768`` so the UI still surfaces a
    # tri-level knob. Model list scoped to the four IDs gemini-cli
    # advertises as its user-selectable menu (per v0.37.1 ``/model``
    # command; the bundle carries internal variants like
    # ``-flash-lite-preview`` and ``-pro-preview-customtools`` that
    # aren't user-facing). Default chosen as ``gemini-3-pro-preview``
    # because ``3.1-pro-preview`` was dropped from the interactive
    # picker in the shipped 0.37.1.
    "gemini-cli": EngineCatalogEntry(
        engine="gemini-cli",
        default_model="gemini-3-pro-preview",
        models=(
            EngineModel(id="gemini-3-pro-preview", label="Gemini 3 Pro Preview"),
            EngineModel(id="gemini-3-flash-preview", label="Gemini 3 Flash Preview"),
            EngineModel(id="gemini-2.5-pro", label="Gemini 2.5 Pro"),
            EngineModel(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
        ),
        reasoning_levels=("low", "medium", "high"),
    ),
    # Issue #355 — OpenHands V1 SDK runs in-process and routes to any
    # provider via litellm-style ``provider/model`` strings. Unlike
    # the three CLI engines above, model IDs MUST carry a provider
    # prefix; the adapter forwards them straight to ``openhands.sdk.LLM``.
    # Phase 0 ships one model per provider as a smoke-test surface;
    # Phase 4 (per the migration plan) expands to the full provider
    # matrix once multi-provider validation is signed off.
    #
    # ``reasoning_levels`` here is the union of provider-supported
    # levels — the per-model ``reasoning_levels`` narrowing reflects
    # each provider's actual acceptance set so the admin UI can show
    # only the knobs that won't be rejected:
    #   - Anthropic models: same five steps as claude-code.
    #   - OpenAI models: same five steps as codex.
    #   - Google models: same three steps as gemini-cli's translation.
    # Phase 4 (#355) — full provider matrix. Each model entry mirrors
    # what the dedicated CLI engines already advertise (claude-code,
    # codex, gemini-cli) so the operator faces the same model menu
    # regardless of which engine is selected. The litellm-style
    # ``provider/model`` prefix is what ``openhands.sdk.LLM`` routes
    # on, so we encode it directly in the model id rather than
    # introducing a separate provider field.
    #
    # Per-model ``reasoning_levels`` narrows to each provider's actual
    # acceptance set (matching the CLI engines' catalog entries),
    # so the admin UI doesn't surface knobs that the underlying
    # provider would reject at runtime.
    "openhands": EngineCatalogEntry(
        engine="openhands",
        default_model="anthropic/claude-opus-4-7",
        models=(
            # ── Anthropic (mirrors claude-code's model list) ────
            EngineModel(
                id="anthropic/claude-opus-4-7",
                label="Claude Opus 4.7 (via OpenHands)",
                reasoning_levels=("low", "medium", "high", "xhigh", "max"),
            ),
            EngineModel(
                id="anthropic/claude-opus-4-6",
                label="Claude Opus 4.6 (via OpenHands)",
                reasoning_levels=("low", "medium", "high", "xhigh", "max"),
            ),
            EngineModel(
                id="anthropic/claude-sonnet-4-6",
                label="Claude Sonnet 4.6 (via OpenHands)",
                reasoning_levels=("low", "medium", "high", "xhigh", "max"),
            ),
            EngineModel(
                id="anthropic/claude-sonnet-4-5",
                label="Claude Sonnet 4.5 (via OpenHands)",
                reasoning_levels=("low", "medium", "high", "xhigh", "max"),
            ),
            EngineModel(
                id="anthropic/claude-haiku-4-5",
                label="Claude Haiku 4.5 (via OpenHands)",
                reasoning_levels=("low", "medium", "high", "xhigh", "max"),
            ),
            # ── OpenAI (mirrors codex's model list) ─────────────
            # gpt-5.5 carries the same backend-side caveat the codex
            # entry documents: announcement-only on 2026-04-25, no
            # round-trip verification yet.
            EngineModel(
                id="openai/gpt-5.5",
                label="GPT-5.5 (via OpenHands)",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
            ),
            EngineModel(
                id="openai/gpt-5.4",
                label="GPT-5.4 (via OpenHands)",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
            ),
            EngineModel(
                id="openai/gpt-5.4-mini",
                label="GPT-5.4 Mini (via OpenHands)",
                reasoning_levels=("minimal", "low", "medium", "high"),
            ),
            EngineModel(
                id="openai/gpt-5.3-codex",
                label="GPT-5.3 Codex (via OpenHands)",
                reasoning_levels=("low", "medium", "high", "xhigh"),
            ),
            EngineModel(
                id="openai/gpt-5.2",
                label="GPT-5.2 (via OpenHands)",
                reasoning_levels=("low", "medium", "high"),
            ),
            # ── Google (mirrors gemini-cli's model list) ────────
            EngineModel(
                id="gemini/gemini-3-pro-preview",
                label="Gemini 3 Pro Preview (via OpenHands)",
                reasoning_levels=("low", "medium", "high"),
            ),
            EngineModel(
                id="gemini/gemini-3-flash-preview",
                label="Gemini 3 Flash Preview (via OpenHands)",
                reasoning_levels=("low", "medium", "high"),
            ),
            EngineModel(
                id="gemini/gemini-2.5-pro",
                label="Gemini 2.5 Pro (via OpenHands)",
                reasoning_levels=("low", "medium", "high"),
            ),
            EngineModel(
                id="gemini/gemini-2.5-flash",
                label="Gemini 2.5 Flash (via OpenHands)",
                reasoning_levels=("low", "medium", "high"),
            ),
        ),
        reasoning_levels=(
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ),
    ),
}


def get_engine_entry(engine: str) -> Optional[EngineCatalogEntry]:
    """Return the catalog entry for ``engine`` or ``None`` if unknown."""
    return ENGINE_CATALOG.get(engine)


def is_valid_model(engine: str, model: str) -> bool:
    """Is ``model`` listed under ``engine`` in the catalog?"""
    entry = get_engine_entry(engine)
    if entry is None:
        return False
    return any(m.id == model for m in entry.models)


def is_valid_reasoning_effort(engine: str, effort: str, model: Optional[str] = None) -> bool:
    """Is ``effort`` a supported reasoning level for this engine/model?

    If ``model`` is provided and it has a non-empty ``reasoning_levels``,
    the per-model list takes precedence. Otherwise the engine-level
    ``reasoning_levels`` is used.
    """
    entry = get_engine_entry(engine)
    if entry is None:
        return False
    if model is not None:
        model_entry = next((m for m in entry.models if m.id == model), None)
        if model_entry is not None and model_entry.reasoning_levels:
            return effort in model_entry.reasoning_levels
    return effort in entry.reasoning_levels


def is_deprecated(engine: str) -> bool:
    """Is ``engine`` flagged as legacy in the catalog (#355 Phase 6)?

    Returns ``False`` when the engine is unknown — preserves the
    pre-#355 behaviour for callers that previously checked
    ``get_engine_entry(...) is not None`` to gate "engine exists"
    decisions. Use this helper when the surface needs a yes/no
    answer (e.g. UI sort key, API response field). For richer
    detail (the ``deprecation_note``), read the entry directly via
    ``get_engine_entry``.
    """
    entry = get_engine_entry(engine)
    return entry is not None and entry.deprecated
