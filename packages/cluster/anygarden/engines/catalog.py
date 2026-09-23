"""Static model and reasoning choices for Codex and Pi.

Pi choices are scoped to an explicit provider; direct endpoint configuration
validates its selection separately. This catalog does not call providers.
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

    supported_versions: tuple[str, ...] = ()
    """#687 — exact CLI versions the agent adapter's version gate accepts.

    Mirrors ``SUPPORTED_VERSIONS`` in ``anygarden_agent.runtime.execution``
    (kept in sync by test; the server does not depend on the agent
    package). The admin UI compares a machine's detected engine version
    against this list to warn before an agent fails with
    ``UNSUPPORTED_RUNTIME``. Empty means "no version gate"."""

    deprecation_note: Optional[str] = None
    """Human-readable rationale shown alongside the legacy badge.

    Pre-#355 entries leave this ``None``. When ``deprecated=True``
    is set later, a one-line note here explains the recommended
    replacement and links to the validation results so the admin
    UI can render context, not just a flag."""


ENGINE_CATALOG: dict[str, EngineCatalogEntry] = {
    # Suggestions only: model IDs depend on the explicitly selected provider.
    # Do not apply a cross-provider default; custom model IDs remain accepted.
    "pi-cli": EngineCatalogEntry(
        engine="pi-cli",
        default_model="",
        models=(EngineModel(id="glm-5.3-flash", label="GLM 5.3 Flash (zai)"),),
        reasoning_levels=(),
        supported_versions=("0.85.1",),
    ),
    # Codex CLI (exec) engine. Reasoning levels come from the backend's
    # own validation error (none/minimal/low/medium/high/xhigh/max — see
    # module docstring). Model list verified by round-tripping an actual
    # ``codex exec`` call with a ChatGPT-account login; Codex does no
    # client-side model-id validation, so the source of truth is what the
    # backend accepts. Other binary IDs (gpt-5.4-pro, gpt-5.2-codex,
    # gpt-5.1-codex-max/mini) return "not supported with a ChatGPT account"
    # and are omitted. (#506 removed the SDK ``codex`` entry.)
    #
    # GPT-5.6 (2026-07-09) ships as three tiers keyed by OpenAI's
    # codenames — sol (flagship), terra (balanced), luna (cost-efficient);
    # all three are live-verified against codex 0.144.1 and add the new
    # ``max`` reasoning level. ``default_model`` is the balanced ``terra``
    # tier — a sensible cost/quality default for everyday agents; operators
    # can pick ``sol`` per-agent when a task warrants the flagship.
    "codex-cli": EngineCatalogEntry(
        engine="codex-cli",
        default_model="gpt-5.6-terra",
        models=(
            EngineModel(
                id="gpt-5.6-sol",
                label="GPT-5.6 Sol",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh", "max"),
            ),
            EngineModel(
                id="gpt-5.6-terra",
                label="GPT-5.6 Terra",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh", "max"),
            ),
            EngineModel(
                id="gpt-5.6-luna",
                label="GPT-5.6 Luna",
                reasoning_levels=("minimal", "low", "medium", "high", "xhigh", "max"),
            ),
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
        reasoning_levels=("minimal", "low", "medium", "high", "xhigh", "max"),
        supported_versions=("0.154.0", "0.155.1"),
    ),
    # Claude Code: ``--effort`` (session flag) accepts
    # ``low/medium/high/xhigh/max``. There is no ``disabled`` option at
    # the CLI — the API-level ``extended_thinking`` / ``adaptive``
    # abstractions are hidden behind the single effort knob. Model IDs
    # come from the shipped binary's symbol table (v2.1.116).

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
