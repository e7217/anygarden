"""Service layer for SkillLibrary — registration and resolution (#119 Phase 1).

Splits responsibilities between the GitHub fetcher (pure IO) and the
DB (persistence / resolution), so tests can drive each side in
isolation and the API handler stays a thin transport adapter.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import AgentSkill, SkillLibraryEntry
from doorae.skills_library.github_fetcher import GitHubFetcher, SkillFetchResult


class _SkillFetcher(Protocol):
    """The minimal contract SkillLibraryService relies on.

    Typing this as a Protocol (not ``GitHubFetcher`` directly) is
    what lets tests swap in a trivial fake — the real fetcher's
    network / error-mapping logic is its own concern.
    """

    async def fetch_skill(
        self, source: str, name: str, rev: str = "HEAD"
    ) -> SkillFetchResult: ...


class SkillLibraryService:
    """Orchestrator between GitHub fetch, DB persistence, and agent resolution."""

    def __init__(
        self,
        session_factory,
        *,
        fetcher: _SkillFetcher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._fetcher: _SkillFetcher = fetcher or GitHubFetcher()

    # ── Registration ─────────────────────────────────────────────

    async def register(
        self,
        *,
        source: str,
        name: str,
        rev: str = "HEAD",
    ) -> SkillLibraryEntry:
        """Fetch from GitHub and upsert into the skill_library row.

        The uniqueness key is ``(source, name, pinned_rev)`` — same
        triple re-uses the existing row (body is refreshed from the
        fetch result); a different ``pinned_rev`` creates a sibling
        row so history is preserved.
        """
        result = await self._fetcher.fetch_skill(source, name, rev)
        content_hash = hashlib.sha256(result.skill_md.encode("utf-8")).hexdigest()

        async with self._session_factory() as db:
            existing = (
                await db.execute(
                    select(SkillLibraryEntry).where(
                        SkillLibraryEntry.source == source,
                        SkillLibraryEntry.name == name,
                        SkillLibraryEntry.pinned_rev == result.commit_sha,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.skill_md = result.skill_md
                existing.scripts_detected = list(result.scripts_detected)
                existing.content_hash = content_hash
                await db.commit()
                await db.refresh(existing)
                return existing

            entry = SkillLibraryEntry(
                source=source,
                name=name,
                pinned_rev=result.commit_sha,
                skill_md=result.skill_md,
                extra_files={},
                scripts_detected=list(result.scripts_detected),
                content_hash=content_hash,
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            return entry

    # ── Attach / detach ─────────────────────────────────────────

    async def attach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        skill_id: str,
    ) -> None:
        """Link an agent to a skill. Idempotent — double-attach is a no-op.

        The caller owns the commit boundary (AgentSkill rows often
        move alongside other mutations inside the API handler), so
        this helper just stages the insert.
        """
        existing = (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.skill_library_id == skill_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        db.add(AgentSkill(agent_id=agent_id, skill_library_id=skill_id))

    async def detach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        skill_id: str,
    ) -> None:
        existing = (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.skill_library_id == skill_id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await db.delete(existing)

    # ── Resolution (called from lifecycle._build_sync_frame) ────

    async def resolve_for_agent(
        self,
        db: AsyncSession,
        agent_id: str,
    ) -> dict[str, str]:
        """Return ``{path_on_agent_disk: body}`` for every skill attached.

        Phase 1 only materializes SKILL.md under ``skills/<name>/``.
        Phase 3 will unpack ``extra_files`` and merge them in here,
        using the same return shape so the lifecycle caller doesn't
        change.
        """
        rows = (
            await db.execute(
                select(SkillLibraryEntry)
                .join(
                    AgentSkill,
                    AgentSkill.skill_library_id == SkillLibraryEntry.id,
                )
                .where(AgentSkill.agent_id == agent_id)
            )
        ).scalars().all()

        files: dict[str, str] = {}
        for entry in rows:
            files[f"skills/{entry.name}/SKILL.md"] = entry.skill_md
        return files
