"""Static catalog of engine models and reasoning levels.

Last refreshed: 2026-04-21 against the locally-installed CLIs
(``claude`` 2.1.116, ``codex`` 0.121.0, ``gemini`` 0.37.1). Values were
verified by probing ``--help``, triggering validation errors, and
reading the shipped binaries' model-name tables — not by trusting the
vendor marketing docs, which lag behind the actual CLIs.

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
        default_model="gpt-5.4",
        models=(
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
    # Direct OpenAI API engine (no Codex CLI). Mirrors the subset of
    # models Doorae agents wire through the plain Responses API.
    "openai": EngineCatalogEntry(
        engine="openai",
        default_model="gpt-5.4",
        models=(
            EngineModel(id="gpt-5.4", label="GPT-5.4"),
            EngineModel(id="gpt-5.4-mini", label="GPT-5.4 Mini"),
        ),
        reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
    ),
    # Direct Anthropic API engine (no Claude Code CLI). Uses the
    # Messages API's extended-thinking vocabulary, which *does* have
    # enable/disable/adaptive semantics (distinct from the CLI's
    # effort scale above).
    "anthropic": EngineCatalogEntry(
        engine="anthropic",
        default_model="claude-opus-4-7",
        models=(
            EngineModel(id="claude-opus-4-7", label="Claude Opus 4.7"),
            EngineModel(id="claude-sonnet-4-6", label="Claude Sonnet 4.6"),
            EngineModel(id="claude-haiku-4-5", label="Claude Haiku 4.5"),
        ),
        reasoning_levels=("disabled", "enabled", "adaptive"),
    ),
    # ── Virtual engines ───────────────────────────────────────────────
    # "codex-extra" routes Codex CLI traffic through the embedded LiteLLM
    # gateway. The model catalog is populated dynamically from
    # ``llm_gateway_models`` at API time, not from this static list — so
    # ``models`` stays empty here. ``default_model`` is left blank; the
    # UI treats "no models registered" as a prompt to add one in the
    # LLM Gateway page.
    "codex-extra": EngineCatalogEntry(
        engine="codex-extra",
        default_model="",
        models=(),
        reasoning_levels=("minimal", "low", "medium", "high", "xhigh"),
    ),
}


# ── Virtual engine support ─────────────────────────────────────────────
#
# Virtual engines are user-facing engine IDs that don't correspond to a
# distinct CLI binary. They reuse an underlying "base" engine's binary
# and adapter, but differ in how credentials / base URLs are wired.
# Today the only virtual engine is ``codex-extra`` (routes through the
# embedded LiteLLM gateway instead of the host ChatGPT-account auth).
VIRTUAL_ENGINE_TO_BASE: dict[str, str] = {
    "codex-extra": "codex",
}


def base_engine(engine: str) -> str:
    """Resolve ``engine`` to its underlying CLI engine.

    Non-virtual engines are returned unchanged. Used by the scheduler
    and machine spawner so they can keep working against the real
    binary name (``codex``) while the DB/UI carry the virtual id.
    """
    return VIRTUAL_ENGINE_TO_BASE.get(engine, engine)


def is_gateway_engine(engine: str) -> bool:
    """Does this engine route through the embedded LLM gateway?"""
    return engine in VIRTUAL_ENGINE_TO_BASE


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
