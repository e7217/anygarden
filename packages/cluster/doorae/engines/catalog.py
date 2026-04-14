"""Static catalog of engine models and reasoning levels.

This is intentionally a hand-maintained dict rather than a live query
against each SDK/CLI. Reasons:

- Several engines (Codex CLI, Claude Code SDK) do not expose a clean
  "list models" API that works without a valid API key. Walking an HTTP
  endpoint per admin page load is a usability regression compared to a
  static dict.
- Reasoning-effort taxonomies differ per model (e.g. GPT-5.4-mini lacks
  ``xhigh``; Haiku lacks extended thinking), which is not discoverable
  from the SDK. The catalog encodes those per-model constraints.
- A stale entry here is a trivial PR, not a runtime failure — the
  agent spawn still works because the adapter falls back to its
  built-in default when the requested model is unknown.

Follow-up work (issue #4) may add a dynamic refresh endpoint that
queries the Anthropic/OpenAI/Google SDKs for live model lists and
merges them with this static baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
                id="gpt-5.2",
                label="GPT-5.2",
                reasoning_levels=("low", "medium", "high"),
            ),
        ),
        reasoning_levels=("low", "medium", "high"),
    ),
    "claude-code": EngineCatalogEntry(
        engine="claude-code",
        default_model="claude-opus-4-6",
        models=(
            EngineModel(
                id="claude-opus-4-6",
                label="Claude Opus 4.6",
                reasoning_levels=("disabled", "enabled", "adaptive"),
            ),
            EngineModel(
                id="claude-sonnet-4-6",
                label="Claude Sonnet 4.6",
                reasoning_levels=("disabled", "enabled", "adaptive"),
            ),
            EngineModel(
                id="claude-haiku-4-5",
                label="Claude Haiku 4.5",
                reasoning_levels=("disabled",),
            ),
        ),
        reasoning_levels=("disabled", "enabled", "adaptive"),
    ),
    "gemini-cli": EngineCatalogEntry(
        engine="gemini-cli",
        default_model="gemini-3-pro",
        models=(
            EngineModel(id="gemini-3-pro", label="Gemini 3 Pro"),
            EngineModel(id="gemini-3-flash", label="Gemini 3 Flash"),
        ),
        reasoning_levels=("low", "medium", "high"),
    ),
    "openai": EngineCatalogEntry(
        engine="openai",
        default_model="gpt-5.4",
        models=(
            EngineModel(id="gpt-5.4", label="GPT-5.4"),
            EngineModel(id="gpt-5.4-mini", label="GPT-5.4 Mini"),
        ),
        reasoning_levels=("low", "medium", "high"),
    ),
    "anthropic": EngineCatalogEntry(
        engine="anthropic",
        default_model="claude-opus-4-6",
        models=(
            EngineModel(id="claude-opus-4-6", label="Claude Opus 4.6"),
            EngineModel(id="claude-sonnet-4-6", label="Claude Sonnet 4.6"),
            EngineModel(id="claude-haiku-4-5", label="Claude Haiku 4.5"),
        ),
        reasoning_levels=("disabled", "enabled", "adaptive"),
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
