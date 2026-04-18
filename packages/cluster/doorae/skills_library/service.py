"""Service layer for SkillLibrary — registration, approval, and resolution.

Splits responsibilities between the GitHub fetcher (pure IO) and the
DB (persistence / resolution), so tests can drive each side in
isolation and the API handler stays a thin transport adapter.

History
-------
- #119 Phase 1 — register / attach / detach / resolve.
- #127 Phase 3 — canonical-tree hash over SKILL.md + extra_files.
- #125 Phase 2 — approve / reject gate + ``skill_library_audits``
  append-only log. Resolution now filters unapproved rows so spawned
  agents never pick up a skill that hasn't cleared admin review.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import (
    AgentSkill,
    SkillLibraryAudit,
    SkillLibraryEntry,
)
from doorae.skills_library.github_fetcher import GitHubFetcher, SkillFetchResult


logger = structlog.get_logger(__name__)


# ── Action constants ─────────────────────────────────────────────────
#
# String constants (not an Enum) because the ``action`` column is
# ``String(32)`` — future phases (stale-check re-approvals, etc.) can
# add new actions without migrating.

ACTION_REGISTER = "register"
ACTION_UPDATE = "update"
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_DELETE = "delete"
ACTION_ATTACH = "attach"
ACTION_DETACH = "detach"
ACTION_GRANDFATHERED = "grandfathered"


# ── Status helpers ──────────────────────────────────────────────────
#
# "status" is a derived view over ``approved_by`` + the audit log.  A
# row's state is one of:
#
# - ``approved``  — ``approved_by IS NOT NULL`` (includes both admin-
#   approved and migration-grandfathered rows).
# - ``rejected``  — latest audit action is ``reject`` (and not later
#   flipped back by an ``approve``).  The row stays at
#   ``approved_by=NULL`` but we distinguish it from plain pending so
#   the UI can show a "Rejected" tab.
# - ``pending``   — everything else (default for newly registered).

STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PENDING = "pending"


@dataclass
class RegisterResult:
    """Return shape for ``SkillLibraryService.register``.

    ``body_changed`` is the signal the API layer uses to decide whether
    to bump the generation of every agent attached to this skill — a
    pure upsert with identical content must NOT force a respawn.
    """
    entry: SkillLibraryEntry
    body_changed: bool


@dataclass
class ApprovalResult:
    """Return shape for approve / reject calls.

    ``entry`` is the refreshed row so callers can re-serialize without
    a second DB round-trip.  ``attached_agent_ids`` is the set of
    agents the API layer should bump (only on approve — rejecting a
    skill doesn't change on-disk agent state because unapproved skills
    weren't materialized in the first place, but keeping the field on
    the dataclass lets both call sites share one code path).
    """
    entry: SkillLibraryEntry
    attached_agent_ids: list[str]


class _SkillFetcher(Protocol):
    """The minimal contract SkillLibraryService relies on.

    Typing this as a Protocol (not ``GitHubFetcher`` directly) is
    what lets tests swap in a trivial fake — the real fetcher's
    network / error-mapping logic is its own concern.
    """

    async def fetch_skill(
        self, source: str, name: str, rev: str = "HEAD"
    ) -> SkillFetchResult: ...


def _canonical_tree_hash(files: dict[str, str]) -> str:
    """Deterministic hash over ``{path: body}`` independent of dict
    order.

    Why not just hash SKILL.md: Phase 3 materializes the whole skill
    directory, and drift in any file (script, reference doc) must
    trip ``body_changed`` so attached agents re-spawn with the fresh
    content.  Path-sorted ``sha256(body)`` concat → final sha256
    gives a short stable digest that changes iff any file's path or
    body changes.

    Separator choice: ``{path}\\n{body_hash}\\n`` — body_hash is a
    64-char hex so collisions between ``"a/b.py" + "X"`` and ``"a"
    + "b.py\\nX"`` are structurally impossible (hex alphabet has no
    newline).
    """
    lines = []
    for path in sorted(files.keys()):
        body_hash = hashlib.sha256(files[path].encode("utf-8")).hexdigest()
        lines.append(f"{path}\n{body_hash}")
    blob = "\n".join(lines)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    # Helper so ``approve`` / ``reject`` / audit rows share the same
    # "now" source — makes tests that freeze the clock simpler.
    return datetime.now(timezone.utc)


class SkillLibraryService:
    """Orchestrator between GitHub fetch, DB persistence, approval, and resolution."""

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
        actor_user_id: Optional[str] = None,
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

        Audit: writes ``register`` (new row) or ``update`` (existing
        row with body_changed=True). An idempotent re-register that
        yields body_changed=False does NOT write an audit row — the
        point of the audit log is to track state changes, not every
        poll of a stable source.
        """
        result = await self._fetcher.fetch_skill(source, name, rev)
        # Canonical tree hash covers SKILL.md plus every extra file.
        # Phase 1 hashed skill_md only; upgrading here means the bump
        # fix (#122) now also fires when a helper script changes.
        tree_blob = {
            f"skills/{name}/SKILL.md": result.skill_md,
            **result.extra_files,
        }
        content_hash = _canonical_tree_hash(tree_blob)

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
                before_hash = existing.content_hash
                body_changed = before_hash != content_hash
                existing.skill_md = result.skill_md
                existing.extra_files = dict(result.extra_files)
                existing.scripts_detected = list(result.scripts_detected)
                existing.content_hash = content_hash
                if body_changed:
                    db.add(
                        SkillLibraryAudit(
                            skill_library_id=existing.id,
                            actor_user_id=actor_user_id,
                            action=ACTION_UPDATE,
                            detail={
                                "source": source,
                                "name": name,
                                "pinned_rev": result.commit_sha,
                                "before_hash": before_hash,
                                "after_hash": content_hash,
                            },
                        )
                    )
                await db.commit()
                await db.refresh(existing)
                return RegisterResult(entry=existing, body_changed=body_changed)

            entry = SkillLibraryEntry(
                source=source,
                name=name,
                pinned_rev=result.commit_sha,
                skill_md=result.skill_md,
                extra_files=dict(result.extra_files),
                scripts_detected=list(result.scripts_detected),
                content_hash=content_hash,
            )
            db.add(entry)
            await db.flush()  # need entry.id for the audit FK
            db.add(
                SkillLibraryAudit(
                    skill_library_id=entry.id,
                    actor_user_id=actor_user_id,
                    action=ACTION_REGISTER,
                    detail={
                        "source": source,
                        "name": name,
                        "pinned_rev": result.commit_sha,
                        "after_hash": content_hash,
                    },
                )
            )
            await db.commit()
            await db.refresh(entry)
            # A brand-new row has no agents attached yet, so body_changed
            # is moot for bump purposes — but we return True anyway so
            # the caller can treat "new" and "updated" uniformly.
            return RegisterResult(entry=entry, body_changed=True)

    # ── Approval gate ───────────────────────────────────────────

    async def approve(
        self,
        *,
        skill_id: str,
        actor_user_id: str,
    ) -> ApprovalResult | None:
        """Mark a skill as approved and record the audit event.

        Idempotent: re-approving an already-approved skill still writes
        an audit row (so double-clicks are visible to reviewers) but
        doesn't re-stamp ``approved_by`` / ``approved_at``. Returns the
        set of attached agent ids so the API layer can bump them once
        and trigger re-materialization (unapproved skills were filtered
        out of the sync frame, so approve is the first moment their
        contents land on disk).

        Returns ``None`` if the skill id doesn't exist — the API layer
        turns that into 404.
        """
        async with self._session_factory() as db:
            entry = (
                await db.execute(
                    select(SkillLibraryEntry).where(
                        SkillLibraryEntry.id == skill_id
                    )
                )
            ).scalar_one_or_none()
            if entry is None:
                return None

            before_hash = entry.content_hash
            if entry.approved_by is None:
                entry.approved_by = actor_user_id
                entry.approved_at = _utcnow()
            db.add(
                SkillLibraryAudit(
                    skill_library_id=entry.id,
                    actor_user_id=actor_user_id,
                    action=ACTION_APPROVE,
                    detail={
                        "before_hash": before_hash,
                        "after_hash": entry.content_hash,
                    },
                )
            )
            attached = list(
                (
                    await db.execute(
                        select(AgentSkill.agent_id).where(
                            AgentSkill.skill_library_id == skill_id
                        )
                    )
                ).scalars().all()
            )
            await db.commit()
            await db.refresh(entry)
            return ApprovalResult(entry=entry, attached_agent_ids=attached)

    async def reject(
        self,
        *,
        skill_id: str,
        actor_user_id: str,
    ) -> ApprovalResult | None:
        """Record a reject decision.

        Reject clears ``approved_by`` / ``approved_at`` if they were
        set (so a previously-approved-then-rejected skill returns to
        the "not in sync frame" state immediately) and writes an
        audit row. If the skill was attached to agents, those agents
        are bumped so the skill contents are removed from their next
        spawn.
        """
        async with self._session_factory() as db:
            entry = (
                await db.execute(
                    select(SkillLibraryEntry).where(
                        SkillLibraryEntry.id == skill_id
                    )
                )
            ).scalar_one_or_none()
            if entry is None:
                return None

            before_hash = entry.content_hash
            was_approved = entry.approved_by is not None
            entry.approved_by = None
            entry.approved_at = None
            db.add(
                SkillLibraryAudit(
                    skill_library_id=entry.id,
                    actor_user_id=actor_user_id,
                    action=ACTION_REJECT,
                    detail={
                        "before_hash": before_hash,
                        "after_hash": entry.content_hash,
                    },
                )
            )
            # Only bump if the skill was previously approved — a
            # reject on a pending skill can't have affected agent
            # on-disk state (resolve_for_agent already filtered it
            # out), so a bump would be gratuitous.
            attached: list[str]
            if was_approved:
                attached = list(
                    (
                        await db.execute(
                            select(AgentSkill.agent_id).where(
                                AgentSkill.skill_library_id == skill_id
                            )
                        )
                    ).scalars().all()
                )
            else:
                attached = []
            await db.commit()
            await db.refresh(entry)
            return ApprovalResult(entry=entry, attached_agent_ids=attached)

    async def get_status(self, db: AsyncSession, skill_id: str) -> Optional[str]:
        """Return ``"approved"`` / ``"rejected"`` / ``"pending"`` for a skill.

        A row is ``rejected`` when its most recent audit action is
        ``reject`` (and ``approved_by`` is NULL).  Otherwise it's
        ``approved`` if ``approved_by`` is set, else ``pending``.  The
        last-audit lookup is only required to separate "pending (never
        reviewed)" from "pending (was rejected)" for UI grouping.
        """
        entry = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
            )
        ).scalar_one_or_none()
        if entry is None:
            return None
        return await self._status_for(db, entry)

    async def _status_for(
        self, db: AsyncSession, entry: SkillLibraryEntry
    ) -> str:
        if entry.approved_by is not None:
            return STATUS_APPROVED
        last = (
            await db.execute(
                select(SkillLibraryAudit.action)
                .where(SkillLibraryAudit.skill_library_id == entry.id)
                .order_by(SkillLibraryAudit.at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last == ACTION_REJECT:
            return STATUS_REJECTED
        return STATUS_PENDING

    async def list_with_status(
        self,
        db: AsyncSession,
        *,
        status: Optional[str] = None,
    ) -> list[tuple[SkillLibraryEntry, str]]:
        """Return ``(entry, status)`` tuples, optionally filtered by
        status.

        The caller (API layer) handles serialization; this method just
        gates which rows cross the boundary. Using one method for both
        "all" and "filtered" keeps the ordering invariant in one place.
        """
        rows = (
            await db.execute(
                select(SkillLibraryEntry).order_by(
                    SkillLibraryEntry.source, SkillLibraryEntry.name
                )
            )
        ).scalars().all()
        results: list[tuple[SkillLibraryEntry, str]] = []
        for entry in rows:
            st = await self._status_for(db, entry)
            if status is None or st == status:
                results.append((entry, st))
        return results

    # ── Audit log ───────────────────────────────────────────────

    async def list_audits(
        self,
        db: AsyncSession,
        skill_id: str,
    ) -> list[SkillLibraryAudit]:
        """Return audit rows for a skill, newest first."""
        rows = (
            await db.execute(
                select(SkillLibraryAudit)
                .where(SkillLibraryAudit.skill_library_id == skill_id)
                .order_by(SkillLibraryAudit.at.desc())
            )
        ).scalars().all()
        return list(rows)

    # ── Delete (was API-local in Phase 1) ───────────────────────

    async def delete(
        self,
        *,
        skill_id: str,
        actor_user_id: Optional[str] = None,
    ) -> tuple[list[str], bool]:
        """Delete a skill and snapshot affected agents for bumping.

        Returns ``(agent_ids, existed)``:
        - ``agent_ids`` — agents that were attached at delete time
          (captured BEFORE the delete because CASCADE wipes them).
        - ``existed`` — ``False`` if the id didn't resolve, so the API
          can return 404.

        Audit row captures the skill metadata before it disappears —
        after the delete the ``skill_library_id`` FK resolves to NULL
        (SET NULL on delete) so the detail payload is how a reviewer
        later identifies the row that was removed.
        """
        async with self._session_factory() as db:
            entry = (
                await db.execute(
                    select(SkillLibraryEntry).where(
                        SkillLibraryEntry.id == skill_id
                    )
                )
            ).scalar_one_or_none()
            if entry is None:
                return [], False

            # Snapshot agent ids BEFORE the delete. The agent_skills
            # CASCADE wipes the link rows atomically with the skill
            # row, so querying afterwards would return an empty list.
            agent_ids = list(
                (
                    await db.execute(
                        select(AgentSkill.agent_id).where(
                            AgentSkill.skill_library_id == skill_id
                        )
                    )
                ).scalars().all()
            )

            db.add(
                SkillLibraryAudit(
                    # skill_library_id stays set briefly, but the
                    # subsequent delete + SET NULL flips it to NULL
                    # post-commit. We keep identifying metadata in
                    # detail so the audit remains meaningful.
                    skill_library_id=entry.id,
                    actor_user_id=actor_user_id,
                    action=ACTION_DELETE,
                    detail={
                        "source": entry.source,
                        "name": entry.name,
                        "pinned_rev": entry.pinned_rev,
                        "before_hash": entry.content_hash,
                    },
                )
            )
            await db.delete(entry)
            await db.commit()
            return agent_ids, True

    # ── Attach / detach ─────────────────────────────────────────

    async def attach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        skill_id: str,
        actor_user_id: Optional[str] = None,
    ) -> bool:
        """Link an agent to a skill. Idempotent — double-attach is a no-op.

        Returns ``True`` if a row was actually inserted, ``False`` if
        the pair was already linked. The API handler keys off this to
        avoid a gratuitous generation bump (and therefore a wasted
        respawn) when the admin re-submits an existing attachment.

        The caller owns the commit boundary.

        NOTE: the API layer is responsible for refusing attach on
        unapproved skills (HTTP 409) — this method stays permissive so
        test fixtures can build arbitrary DB states without routing
        through the approval gate.
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
        db.add(
            SkillLibraryAudit(
                skill_library_id=skill_id,
                actor_user_id=actor_user_id,
                action=ACTION_ATTACH,
                detail={"agent_id": agent_id},
            )
        )
        return True

    async def detach(
        self,
        db: AsyncSession,
        *,
        agent_id: str,
        skill_id: str,
        actor_user_id: Optional[str] = None,
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
        db.add(
            SkillLibraryAudit(
                skill_library_id=skill_id,
                actor_user_id=actor_user_id,
                action=ACTION_DETACH,
                detail={"agent_id": agent_id},
            )
        )
        return True

    # ── Resolution (called from lifecycle._build_sync_frame) ────

    async def resolve_for_agent(
        self,
        db: AsyncSession,
        agent_id: str,
    ) -> dict[str, str]:
        """Return ``{path_on_agent_disk: body}`` for every *approved* skill attached.

        Phase 3: yields SKILL.md *and* every entry from
        ``SkillLibraryEntry.extra_files`` so the whole skill
        directory lands in the sync frame.

        Phase 2 (#125): unapproved rows are filtered out here. If an
        attached row is unapproved we additionally structlog.warn —
        the UI prevents attaching unapproved skills, so an unapproved
        attachment in the wild means either a race (approve→reject
        after attach) or a manual DB edit, both of which an operator
        wants to see in the logs.

        Collision resolution between multiple skills is last-write-wins
        within this function; AgentFile precedence is applied by the
        caller (``lifecycle._build_sync_frame``).
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
            if entry.approved_by is None:
                logger.warning(
                    "skill_library.resolve_skipped_unapproved",
                    agent_id=agent_id,
                    skill_id=entry.id,
                    source=entry.source,
                    name=entry.name,
                )
                continue
            files[f"skills/{entry.name}/SKILL.md"] = entry.skill_md
            for rel_path, body in (entry.extra_files or {}).items():
                files[rel_path] = body
        return files
