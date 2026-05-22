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
- #120 adds an *agent-authoring* branch (``create_from_agent`` and
  friends) that shares the canonical content-hash helper with the
  GitHub-backed ``register`` path but persists directly from
  caller-supplied bodies instead of fetching remotely. Keeping both
  branches in one service keeps ``resolve_for_agent`` and the link
  table semantics in one place; hash parity is what lets an admin
  promote an agent-authored row into the shared library without
  re-hashing. ``resolve_for_agent`` ORs on ``created_by_agent_id``
  so an author always sees their own skills regardless of approval.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import (
    AgentSkill,
    SkillLibraryAudit,
    SkillLibraryEntry,
)
from anygarden.skills_library.github_fetcher import (
    GitHubFetchError,
    GitHubFetcher,
    GitHubRateLimitError,
    SkillFetchResult,
)


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


# ── Exceptions (#120 agent-authoring) ────────────────────────────────


class SkillOwnershipError(RuntimeError):
    """Raised when an agent tries to mutate a skill it doesn't own.

    The MCP layer maps this to an ``isError=True`` tool result rather
    than a JSON-RPC error so the LLM can read the message and decide
    what to do.
    """


class SkillNameConflictError(RuntimeError):
    """Raised when ``create_from_agent`` sees an existing
    ``(created_by_agent_id, name)`` pair.  Author scope, not global —
    two different agents may independently both have a skill called
    ``notes``.
    """


class SkillNotFoundError(RuntimeError):
    """Raised when a skill id doesn't resolve (or isn't visible)."""


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

    async def resolve_head_sha(
        self, source: str, rev: str = "HEAD"
    ) -> str: ...


@dataclass
class StaleCheckResult:
    """Return shape for ``SkillLibraryService.check_stale`` (#126).

    ``stale`` is the verdict the UI cares about; ``current_sha`` lets
    the caller log exactly what drift they observed so post-mortems
    can correlate with upstream commit timelines. ``error`` is set
    when the probe failed (rate limit, repo gone) — the UI renders
    those as a separate "couldn't check" state rather than silently
    treating them as up-to-date.
    """
    skill_id: str
    pinned_rev: str
    current_sha: Optional[str]
    stale: bool
    error: Optional[str] = None


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
        """Return ``{path_on_agent_disk: body}`` for every skill visible
        to the agent.

        Visibility (#125 + #120): approved rows OR rows authored by
        this agent (self-approved). Pre-#125 rows are grandfathered in
        migration 020 so they already carry ``approved_by`` and fall
        under the first branch.

        Phase 3: yields SKILL.md *and* every entry from
        ``SkillLibraryEntry.extra_files`` so the whole skill directory
        lands in the sync frame. Collision resolution between multiple
        skills is last-write-wins within this function; AgentFile
        precedence is applied by the caller
        (``lifecycle._build_sync_frame``).

        #120 self-authored skills are visible to their author
        regardless of approval state — this pre-empts #125's gate so
        an agent never loses a skill it just created to itself via
        the MCP tool channel.

        If an attached row slips through unapproved and is not self-
        authored (DB edit, race between approve→reject and attach),
        the SQL filter already excludes it; the Python loop keeps
        a defensive check + structlog.warn so operators see the
        anomaly in logs.
        """
        rows = (
            await db.execute(
                select(SkillLibraryEntry)
                .join(
                    AgentSkill,
                    AgentSkill.skill_library_id == SkillLibraryEntry.id,
                )
                .where(
                    AgentSkill.agent_id == agent_id,
                    or_(
                        SkillLibraryEntry.approved_by.is_not(None),
                        SkillLibraryEntry.created_by_agent_id == agent_id,
                    ),
                )
            )
        ).scalars().all()

        files: dict[str, str] = {}
        for entry in rows:
            # Self-authored rows bypass the approval gate — #120.
            is_self_authored = entry.created_by_agent_id == agent_id
            if entry.approved_by is None and not is_self_authored:
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

    # ── Stale check (#126) ──────────────────────────────────────

    async def check_stale(
        self,
        db: AsyncSession,
        skill_id: str,
    ) -> Optional[StaleCheckResult]:
        """Probe one skill's upstream HEAD and compare against pinned_rev.

        Only issues the lightweight tree-head request (via
        ``resolve_head_sha``) — full body fetch happens on refresh.
        Agent-authored rows (``source`` starting with
        ``agent-authored:``) have no upstream to poll, so they're
        short-circuited to ``stale=False``.

        Returns ``None`` when the skill id doesn't resolve so the API
        caller can translate to 404. Any GitHub error short-circuits
        with ``stale=False`` + ``error`` populated — we don't want a
        transient 5xx or rate-limit to flip every skill into the
        "Update available" state.
        """
        entry = (
            await db.execute(
                select(SkillLibraryEntry).where(
                    SkillLibraryEntry.id == skill_id
                )
            )
        ).scalar_one_or_none()
        if entry is None:
            return None
        if entry.source.startswith(self._AGENT_AUTHORED_SOURCE_PREFIX):
            return StaleCheckResult(
                skill_id=entry.id,
                pinned_rev=entry.pinned_rev,
                current_sha=entry.pinned_rev,
                stale=False,
            )
        try:
            current_sha = await self._fetcher.resolve_head_sha(entry.source, "HEAD")
        except GitHubRateLimitError as exc:
            return StaleCheckResult(
                skill_id=entry.id,
                pinned_rev=entry.pinned_rev,
                current_sha=None,
                stale=False,
                error=f"rate limited: {exc}",
            )
        except GitHubFetchError as exc:
            return StaleCheckResult(
                skill_id=entry.id,
                pinned_rev=entry.pinned_rev,
                current_sha=None,
                stale=False,
                error=f"fetch failed: {exc}",
            )
        return StaleCheckResult(
            skill_id=entry.id,
            pinned_rev=entry.pinned_rev,
            current_sha=current_sha,
            stale=current_sha != entry.pinned_rev,
        )

    async def check_all_stale(self) -> dict[str, StaleCheckResult]:
        """Probe every registered skill. Used by the stale-check cron.

        Stops the loop early on a rate-limit error — skills we haven't
        probed yet stay as "unknown" (absent from the returned dict)
        so the next cron tick can pick up where this one left off.
        Other per-skill errors are recorded but don't halt the loop.
        """
        async with self._session_factory() as db:
            entries = (
                await db.execute(select(SkillLibraryEntry))
            ).scalars().all()

        results: dict[str, StaleCheckResult] = {}
        for entry in entries:
            if entry.source.startswith(self._AGENT_AUTHORED_SOURCE_PREFIX):
                results[entry.id] = StaleCheckResult(
                    skill_id=entry.id,
                    pinned_rev=entry.pinned_rev,
                    current_sha=entry.pinned_rev,
                    stale=False,
                )
                continue
            try:
                current_sha = await self._fetcher.resolve_head_sha(
                    entry.source, "HEAD"
                )
            except GitHubRateLimitError as exc:
                logger.warning(
                    "skill_library.stale_check_rate_limited",
                    skill_id=entry.id,
                    source=entry.source,
                    error=str(exc),
                )
                # Stop the sweep — subsequent probes would just pile
                # more errors on the same rate-limit window.
                break
            except GitHubFetchError as exc:
                logger.warning(
                    "skill_library.stale_check_failed",
                    skill_id=entry.id,
                    source=entry.source,
                    error=str(exc),
                )
                results[entry.id] = StaleCheckResult(
                    skill_id=entry.id,
                    pinned_rev=entry.pinned_rev,
                    current_sha=None,
                    stale=False,
                    error=f"fetch failed: {exc}",
                )
                continue
            results[entry.id] = StaleCheckResult(
                skill_id=entry.id,
                pinned_rev=entry.pinned_rev,
                current_sha=current_sha,
                stale=current_sha != entry.pinned_rev,
            )
        return results

    # ── Agent-authoring (#120) ──────────────────────────────────

    _AGENT_AUTHORED_SOURCE_PREFIX = "agent-authored:"

    async def create_from_agent(
        self,
        *,
        agent_id: str,
        name: str,
        description: str,
        body: str,
        extra_files: Optional[dict[str, str]] = None,
    ) -> SkillLibraryEntry:
        """Persist an agent-authored skill and auto-attach it to the author.

        Canonical content-hash is computed with the same helper the
        GitHub-backed path uses — this makes a later ``promote`` a
        no-op at the content layer (the hash doesn't change, so
        ``body_changed`` stays stable).  ``source`` is stamped with
        ``agent-authored:<agent_id>`` so the ``(source, name,
        pinned_rev)`` unique constraint that protects GitHub registrations
        stays non-colliding here: each agent gets its own synthetic
        source namespace.

        Name uniqueness per author is enforced at the service layer
        because the DB constraint keys off ``(source, name, pinned_rev)``
        — a second create with the same ``name`` would pass DB
        validation but break ``list_by_owner`` UX and make lookup
        ambiguous.
        """
        extras = dict(extra_files or {})
        skill_md_path = f"skills/{name}/SKILL.md"
        tree_blob = {skill_md_path: body, **extras}
        content_hash = _canonical_tree_hash(tree_blob)
        source = f"{self._AGENT_AUTHORED_SOURCE_PREFIX}{agent_id}"

        async with self._session_factory() as db:
            conflict = (
                await db.execute(
                    select(SkillLibraryEntry).where(
                        SkillLibraryEntry.created_by_agent_id == agent_id,
                        SkillLibraryEntry.name == name,
                    )
                )
            ).scalar_one_or_none()
            if conflict is not None:
                raise SkillNameConflictError(
                    f"skill named {name!r} already exists for this agent"
                )

            entry = SkillLibraryEntry(
                source=source,
                name=name,
                # ``pinned_rev`` is required (NOT NULL) in the Phase 1
                # migration; agent-authored rows use the content hash
                # as a stand-in so repeated updates get distinct
                # "revisions" without collision on the unique
                # constraint.  Not a git SHA, but the column's
                # consumers (UI display, resolve query) treat it as an
                # opaque identifier.
                pinned_rev=content_hash,
                skill_md=body,
                extra_files=extras,
                scripts_detected=sorted(extras.keys()),
                content_hash=content_hash,
                created_by_agent_id=agent_id,
                approved_by=None,
            )
            db.add(entry)
            await db.flush()
            db.add(AgentSkill(agent_id=agent_id, skill_library_id=entry.id))
            # Stash description on agents_md? No — description is a
            # UI-only hint for "list_my_skills"; keep it out of the
            # DB row to avoid a schema change and use the first line
            # of ``body`` as a proxy.  Accepted in the tool signature
            # so the MCP schema is discoverable.
            _ = description
            await db.commit()
            await db.refresh(entry)
            return entry

    async def update_by_owner(
        self,
        *,
        agent_id: str,
        skill_id: str,
        body: Optional[str] = None,
        extra_files: Optional[dict[str, str]] = None,
    ) -> SkillLibraryEntry:
        """Rewrite body / extras on a skill the caller authored.

        Ownership is enforced — any other agent's skill trips
        ``SkillOwnershipError`` which the MCP layer maps to an
        ``isError`` tool result.  Admin-shared rows (``created_by_agent_id
        IS NULL``) are likewise not owned by any agent and so are
        untouchable from this path; the admin still uses the
        ``/api/v1/admin/skills`` REST surface for those.
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
                raise SkillNotFoundError(f"skill {skill_id!r} not found")
            if entry.created_by_agent_id != agent_id:
                raise SkillOwnershipError(
                    f"skill {skill_id!r} is not owned by agent {agent_id!r}"
                )

            if body is not None:
                entry.skill_md = body
            if extra_files is not None:
                entry.extra_files = dict(extra_files)
                entry.scripts_detected = sorted(extra_files.keys())

            # Recompute content_hash from the (possibly updated) tree
            # so later drift detection stays accurate.
            tree_blob = {
                f"skills/{entry.name}/SKILL.md": entry.skill_md,
                **(entry.extra_files or {}),
            }
            entry.content_hash = _canonical_tree_hash(tree_blob)
            # Keep pinned_rev synced with the hash for agent-authored rows
            # (see ``create_from_agent`` for rationale).
            entry.pinned_rev = entry.content_hash
            await db.commit()
            await db.refresh(entry)
            return entry

    async def list_by_owner(
        self,
        *,
        agent_id: str,
    ) -> list[SkillLibraryEntry]:
        """All skills authored by ``agent_id``, ordered by name."""
        async with self._session_factory() as db:
            rows = (
                await db.execute(
                    select(SkillLibraryEntry)
                    .where(SkillLibraryEntry.created_by_agent_id == agent_id)
                    .order_by(SkillLibraryEntry.name)
                )
            ).scalars().all()
            return list(rows)

    async def delete_by_owner(
        self,
        *,
        agent_id: str,
        skill_id: str,
    ) -> bool:
        """Delete an agent-authored skill (and cascade link rows).

        Ownership-checked — same semantics as ``update_by_owner``.
        Returns ``True`` when a row was actually removed so the MCP
        tool can surface the outcome; the only way to reach ``False``
        is a row that vanished between the fetch and the delete (a
        race we don't actively trigger, but keeping the return shape
        symmetric with ``attach/detach`` is useful).
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
                raise SkillNotFoundError(f"skill {skill_id!r} not found")
            if entry.created_by_agent_id != agent_id:
                raise SkillOwnershipError(
                    f"skill {skill_id!r} is not owned by agent {agent_id!r}"
                )
            await db.delete(entry)
            await db.commit()
            return True

    async def promote_to_shared(
        self,
        *,
        skill_id: str,
        admin_user_id: str,
    ) -> SkillLibraryEntry:
        """Admin-only: flip an agent-authored row into a shared library
        entry.

        The semantics match "approve" in the #125 workflow: stamp
        ``approved_by`` with the admin's user id and null out
        ``created_by_agent_id`` so the row no longer belongs to any
        one agent.  After promotion, ``attach`` from the existing REST
        surface can fan the skill out to other agents.
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
                raise SkillNotFoundError(f"skill {skill_id!r} not found")
            entry.created_by_agent_id = None
            entry.approved_by = admin_user_id
            await db.commit()
            await db.refresh(entry)
            return entry
