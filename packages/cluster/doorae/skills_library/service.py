"""Service layer for SkillLibrary — registration and resolution (#119 Phase 1).

Splits responsibilities between the GitHub fetcher (pure IO) and the
DB (persistence / resolution), so tests can drive each side in
isolation and the API handler stays a thin transport adapter.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import AgentSkill, SkillLibraryEntry
from doorae.skills_library.github_fetcher import GitHubFetcher, SkillFetchResult


@dataclass
class RegisterResult:
    """Return shape for ``SkillLibraryService.register``.

    ``body_changed`` is the signal the API layer uses to decide whether
    to bump the generation of every agent attached to this skill — a
    pure upsert with identical content must NOT force a respawn.
    """
    entry: SkillLibraryEntry
    body_changed: bool


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
    ) -> RegisterResult:
        """Fetch from GitHub and upsert into the skill_library row.

        The uniqueness key is ``(source, name, pinned_rev)`` — same
        triple re-uses the existing row (body is refreshed from the
        fetch result); a different ``pinned_rev`` creates a sibling
        row so history is preserved.

        Returns a ``RegisterResult`` so the caller knows whether the
        persisted body actually changed (new row or different hash) —
        the API handler uses that to bump the generation of every
        attached agent only when a respawn is actually warranted.
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
                body_changed = existing.content_hash != content_hash
                existing.skill_md = result.skill_md
                existing.scripts_detected = list(result.scripts_detected)
                existing.content_hash = content_hash
                await db.commit()
                await db.refresh(existing)
                return RegisterResult(entry=existing, body_changed=body_changed)

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
            # A brand-new row has no agents attached yet, so body_changed
            # is moot for bump purposes — but we return True anyway so
            # the caller can treat "new" and "updated" uniformly.
            return RegisterResult(entry=entry, body_changed=True)

    # ── Attach / detach ─────────────────────────────────────────

    async def attach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        skill_id: str,
    ) -> bool:
        """Link an agent to a skill. Idempotent — double-attach is a no-op.

        Returns ``True`` if a row was actually inserted, ``False`` if
        the pair was already linked. The API handler keys off this to
        avoid a gratuitous generation bump (and therefore a wasted
        respawn) when the admin re-submits an existing attachment.

        The caller owns the commit boundary.
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
            return False
        db.add(AgentSkill(agent_id=agent_id, skill_library_id=skill_id))
        return True

    async def detach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        skill_id: str,
    ) -> bool:
        """Reverse of ``attach``. Returns ``True`` only when a link row
        actually existed and was removed — same bump-gating purpose.
        """
        existing = (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.skill_library_id == skill_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return False
        await db.delete(existing)
        return True

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
