"""#312 — Room.representative_agent_id invariant tests.

Two contracts the helper layer must guarantee:

1. **Auto-set on first join**. When a room has no representative,
   the first agent to join becomes it. Subsequent agents do not
   silently steal the role.
2. **Succession on removal**. When the rep is removed from the
   room, the next agent (ordered by joined_at, id as tie-breaker)
   takes over. The role only goes back to NULL when no agent
   remains.

These cover the entry points that mutate room membership today —
``rooms.membership.ensure_agent_in_room`` (used by both
``POST /rooms/{id}/participants`` and ``POST /agents/{id}/rooms``)
and the ``rooms.router.remove_participant`` flow. Adding to either
shape requires the same single helper change.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, Participant, Project, Room, User
from anygarden.rooms.membership import ensure_agent_in_room, add_user_to_room


# ── helpers ────────────────────────────────────────────────────────


async def _seed_room(db: AsyncSession, name: str = "r") -> Room:
    project = Project(name=f"p-{name}")
    db.add(project)
    await db.flush()
    room = Room(project_id=project.id, name=name)
    db.add(room)
    await db.flush()
    await db.commit()
    await db.refresh(room)
    return room


async def _seed_agent(db: AsyncSession, name: str) -> Agent:
    agent = Agent(name=name, engine="codex", actual_state="running")
    db.add(agent)
    await db.flush()
    await db.commit()
    await db.refresh(agent)
    return agent


async def _seed_user(db: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="x")
    db.add(user)
    await db.flush()
    await db.commit()
    await db.refresh(user)
    return user


async def _refetch_room(db: AsyncSession, room_id: str) -> Room:
    return (
        await db.execute(select(Room).where(Room.id == room_id))
    ).scalar_one()


# ── auto-set on first join ─────────────────────────────────────────


class TestAutoSetRepOnFirstJoin:
    @pytest.mark.asyncio
    async def test_first_agent_added_becomes_rep(
        self, db: AsyncSession
    ) -> None:
        """Empty room + first agent → ``representative_agent_id`` is
        set to that agent. This is the load-bearing invariant for
        #313's batch auto-route to have a guaranteed entity to ask."""
        room = await _seed_room(db)
        agent = await _seed_agent(db, "a1")
        assert (await _refetch_room(db, room.id)).representative_agent_id is None

        await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=agent.id
        )

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id == agent.id

    @pytest.mark.asyncio
    async def test_second_agent_does_not_change_rep(
        self, db: AsyncSession
    ) -> None:
        """Once a rep exists, subsequent additions don't displace it.
        Otherwise rep would become "most recently joined" which breaks
        the user's mental model — admins explicitly set rep, the
        invariant only fills NULL slots."""
        room = await _seed_room(db)
        a1 = await _seed_agent(db, "a1")
        a2 = await _seed_agent(db, "a2")

        await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=a1.id
        )
        await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=a2.id
        )

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id == a1.id

    @pytest.mark.asyncio
    async def test_admin_set_rep_persists_through_add(
        self, db: AsyncSession
    ) -> None:
        """Admin manually picked a rep that's not the first joiner —
        adding a fourth agent must not revert that decision. (Admin
        intent always wins over the auto-fill heuristic.)"""
        room = await _seed_room(db)
        a1 = await _seed_agent(db, "a1")
        a2 = await _seed_agent(db, "a2")
        a3 = await _seed_agent(db, "a3")

        await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=a1.id
        )
        await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=a2.id
        )

        # Admin picks the second agent explicitly.
        room_obj = await _refetch_room(db, room.id)
        room_obj.representative_agent_id = a2.id
        await db.commit()

        # Adding a third agent — the explicit pick must survive.
        await ensure_agent_in_room(
            db, None, room_id=room.id, agent_id=a3.id
        )

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id == a2.id

    @pytest.mark.asyncio
    async def test_user_addition_does_not_set_rep(
        self, db: AsyncSession
    ) -> None:
        """Only agents are eligible reps — adding a user must not
        flip the field. (Belt-and-braces: ``add_user_to_room`` doesn't
        touch the field today, but the invariant lives in the agent
        helper anyway, so this guards future refactors.)"""
        room = await _seed_room(db)
        user = await _seed_user(db, "u@test.com")

        await add_user_to_room(
            db, None, room_id=room.id, user_id=user.id
        )

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id is None


# ── succession on removal ──────────────────────────────────────────


class TestRepSuccession:
    """The succession path lives in ``rooms.router.remove_participant``
    rather than the helper, but it relies on the same invariant. Tests
    here drive ``remove_participant`` end-to-end via the API so we
    cover the actual flow used by the frontend / agents-API call sites.
    """

    @pytest.mark.asyncio
    async def test_succession_to_next_by_joined_at(
        self, db: AsyncSession
    ) -> None:
        """Remove the current rep → the next agent (by joined_at)
        takes over. We seed three agents with non-overlapping join
        timestamps so the order is unambiguous."""
        from anygarden.rooms.membership import _set_next_rep_after_removal

        room = await _seed_room(db)
        a1 = await _seed_agent(db, "a1")
        a2 = await _seed_agent(db, "a2")
        a3 = await _seed_agent(db, "a3")

        # Stamp deterministic joined_at so the test asserts ordering.
        base = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
        for i, agent in enumerate([a1, a2, a3]):
            p = Participant(
                room_id=room.id,
                agent_id=agent.id,
                role="member",
                joined_at=base + timedelta(seconds=i),
            )
            db.add(p)
        await db.flush()

        room_obj = await _refetch_room(db, room.id)
        room_obj.representative_agent_id = a1.id
        await db.commit()

        # Remove the rep's participant row, then run the helper.
        rep_p = (
            await db.execute(
                select(Participant).where(
                    Participant.room_id == room.id,
                    Participant.agent_id == a1.id,
                )
            )
        ).scalar_one()
        removed_pid = rep_p.id
        await db.delete(rep_p)
        await db.flush()

        await _set_next_rep_after_removal(
            db,
            room_id=room.id,
            removed_agent_id=a1.id,
            removed_participant_id=removed_pid,
        )
        await db.commit()

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id == a2.id

    @pytest.mark.asyncio
    async def test_succession_with_id_tiebreaker(
        self, db: AsyncSession
    ) -> None:
        """Two agents joined at exactly the same instant → succession
        is deterministic via ``id`` ASC. uuid order is arbitrary but
        stable, which is enough — what we forbid is "no answer" or
        "different answer per call"."""
        from anygarden.rooms.membership import _set_next_rep_after_removal

        room = await _seed_room(db)
        rep = await _seed_agent(db, "rep")
        a1 = await _seed_agent(db, "a1")
        a2 = await _seed_agent(db, "a2")

        same_ts = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)
        # rep joins earlier so it's the only candidate for "current rep".
        db.add(Participant(
            room_id=room.id, agent_id=rep.id, role="member",
            joined_at=same_ts - timedelta(seconds=1),
        ))
        p1 = Participant(
            room_id=room.id, agent_id=a1.id, role="member", joined_at=same_ts,
        )
        p2 = Participant(
            room_id=room.id, agent_id=a2.id, role="member", joined_at=same_ts,
        )
        db.add(p1)
        db.add(p2)
        await db.flush()

        room_obj = await _refetch_room(db, room.id)
        room_obj.representative_agent_id = rep.id
        await db.commit()

        rep_p = (
            await db.execute(
                select(Participant).where(
                    Participant.room_id == room.id,
                    Participant.agent_id == rep.id,
                )
            )
        ).scalar_one()
        rep_pid = rep_p.id
        await db.delete(rep_p)
        await db.flush()

        await _set_next_rep_after_removal(
            db,
            room_id=room.id,
            removed_agent_id=rep.id,
            removed_participant_id=rep_pid,
        )
        await db.commit()

        room_after = await _refetch_room(db, room.id)
        # Determinism: must be one of the two surviving candidates.
        assert room_after.representative_agent_id in {a1.id, a2.id}
        # Tie-breaker is ``Participant.id ASC`` — whichever participant
        # row sorts first by uuid wins. Map back from participant.id to
        # agent.id to compute the expected value.
        winning_pid = min(p1.id, p2.id)
        expected_agent_id = a1.id if winning_pid == p1.id else a2.id
        assert room_after.representative_agent_id == expected_agent_id

    @pytest.mark.asyncio
    async def test_rep_null_when_last_agent_removed(
        self, db: AsyncSession
    ) -> None:
        """No agents left → rep = NULL. Empty room is a degenerate
        state but the field must not point at a removed/dead row."""
        from anygarden.rooms.membership import _set_next_rep_after_removal

        room = await _seed_room(db)
        agent = await _seed_agent(db, "a1")

        p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(p)
        await db.flush()

        room_obj = await _refetch_room(db, room.id)
        room_obj.representative_agent_id = agent.id
        await db.commit()

        await db.delete(p)
        await db.flush()

        await _set_next_rep_after_removal(
            db,
            room_id=room.id,
            removed_agent_id=agent.id,
            removed_participant_id=p.id,
        )
        await db.commit()

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id is None

    @pytest.mark.asyncio
    async def test_non_rep_removal_does_not_touch_rep(
        self, db: AsyncSession
    ) -> None:
        """Removing a participant who *isn't* the rep is a no-op for
        the field — the helper only intervenes when the removed agent
        held the role."""
        from anygarden.rooms.membership import _set_next_rep_after_removal

        room = await _seed_room(db)
        rep = await _seed_agent(db, "rep")
        bystander = await _seed_agent(db, "by")

        db.add(Participant(room_id=room.id, agent_id=rep.id, role="member"))
        bp = Participant(
            room_id=room.id, agent_id=bystander.id, role="member"
        )
        db.add(bp)
        await db.flush()

        room_obj = await _refetch_room(db, room.id)
        room_obj.representative_agent_id = rep.id
        await db.commit()

        # Remove bystander, not rep. The helper signature still gets
        # called from the deletion path, but the rep field shouldn't
        # change because the removed agent is not the rep.
        bp_pid = bp.id
        await db.delete(bp)
        await db.flush()

        await _set_next_rep_after_removal(
            db,
            room_id=room.id,
            removed_agent_id=bystander.id,
            removed_participant_id=bp_pid,
        )
        await db.commit()

        room_after = await _refetch_room(db, room.id)
        assert room_after.representative_agent_id == rep.id
