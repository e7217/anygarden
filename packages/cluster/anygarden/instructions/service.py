"""Server side of disk-sourced agent instructions (#908).

On a machine that advertises :data:`CAPABILITY` the agent's ``AGENTS.md``
user area and ``skills/`` files live on that machine's disk. The server
keeps the last synced snapshot in ``agents.agents_md`` and the ``skills/``
rows of ``agent_files`` and records every accepted version in
``agent_instruction_revisions``.

Every instructions write goes through :func:`write_instructions`:

- ``disk`` — the placed machine is online: the change is applied on disk
  and the machine's reply becomes the new snapshot.
- ``disk_offline`` — the machine is offline: instructions are read-only.
- ``server`` — no capable placement (older daemon, never placed, or a
  machine that has not seeded the agent yet): the snapshot is edited
  directly and seeds the disk on the next start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.agent_files import AgentFilePathError, validate_agent_file_path
from anygarden.db.models import Agent, AgentFile, AgentInstructionRevision, Machine
from anygarden.machine.protocol.frames import (
    AgentInstructionsResultFrame,
    AgentInstructionsUpdateFrame,
)
from anygarden.scheduler.machine_bus import MachineBus, MachineRequestError

logger = structlog.get_logger(__name__)

CAPABILITY = "agent_instructions_disk_v1"
SKILLS_PREFIX = "skills/"
INSTRUCTION_REVISIONS_KEPT = 50

InstructionsMode = Literal["disk", "disk_offline", "server"]
Source = Literal["user", "agent", "system"]
Operation = Literal["write_agents_md", "write_skill", "delete_skill", "replace"]


@dataclass
class Snapshot:
    agents_md: str = ""
    skills: dict[str, str] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"agents_md": self.agents_md, "skills": dict(self.skills)}


def is_instruction_path(path: str) -> bool:
    return path.startswith(SKILLS_PREFIX)


def _normalise(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").strip("\n").rstrip()


def machine_supports(machine: Machine | None) -> bool:
    return machine is not None and CAPABILITY in set(machine.control_capabilities or [])


def instructions_mode(
    agent: Agent, machine: Machine | None, bus: MachineBus | None
) -> InstructionsMode:
    if (
        agent.placed_on_machine_id is None
        or machine is None
        or machine.id != agent.placed_on_machine_id
        or not machine_supports(machine)
    ):
        return "server"
    if bus is not None and bus.is_connected(machine.id):
        return "disk"
    return "disk_offline"


async def current_snapshot(db: AsyncSession, agent: Agent) -> Snapshot:
    rows = (
        await db.scalars(
            select(AgentFile).where(
                AgentFile.agent_id == agent.id, AgentFile.path.startswith(SKILLS_PREFIX)
            )
        )
    ).all()
    return Snapshot(
        agents_md=_normalise(agent.agents_md),
        skills={row.path: row.content for row in sorted(rows, key=lambda row: row.path)},
    )


def _validated(snapshot: Snapshot) -> Snapshot:
    for path in snapshot.skills:
        validate_agent_file_path(path)
        if not is_instruction_path(path):
            raise AgentFilePathError(f"{path!r} is not under skills/")
    return Snapshot(_normalise(snapshot.agents_md), dict(sorted(snapshot.skills.items())))


async def record_snapshot(
    db: AsyncSession,
    agent: Agent,
    snapshot: Snapshot,
    *,
    source: Source,
    by_user_id: str | None = None,
    restored_from: int | None = None,
) -> int:
    """Store ``snapshot`` as the agent's next revision (caller commits)."""
    snapshot = _validated(snapshot)
    agent.agents_md = snapshot.agents_md or None
    existing = {
        row.path: row
        for row in (
            await db.scalars(
                select(AgentFile).where(
                    AgentFile.agent_id == agent.id, AgentFile.path.startswith(SKILLS_PREFIX)
                )
            )
        ).all()
    }
    for path, row in existing.items():
        if path not in snapshot.skills:
            await db.delete(row)
    for path, content in snapshot.skills.items():
        row = existing.get(path)
        if row is None:
            db.add(AgentFile(agent_id=agent.id, path=path, content=content))
        elif row.content != content:
            row.content = content
    revision = int(agent.instructions_revision or 0) + 1
    agent.instructions_revision = revision
    agent.instructions_synced_at = datetime.now(timezone.utc)
    db.add(
        AgentInstructionRevision(
            agent_id=agent.id,
            revision=revision,
            source=source,
            by_user_id=by_user_id,
            restored_from=restored_from,
            agents_md=snapshot.agents_md,
            skills=snapshot.skills,
        )
    )
    await db.execute(
        delete(AgentInstructionRevision).where(
            AgentInstructionRevision.agent_id == agent.id,
            AgentInstructionRevision.revision <= revision - INSTRUCTION_REVISIONS_KEPT,
        )
    )
    return revision


# ── Machine → server ────────────────────────────────────────────────


async def apply_machine_update(
    db: AsyncSession, *, machine_id: str, data: dict[str, Any]
) -> dict[str, Any] | None:
    """Accept a disk snapshot from the agent's machine; return the ack frame.

    Returns ``None`` (no ack) for frames that do not belong to this
    machine; the daemon resends unacknowledged snapshots later.
    """
    try:
        frame = AgentInstructionsUpdateFrame.model_validate(data)
    except ValidationError:
        return None
    agent = await db.get(Agent, frame.agent_id)
    if agent is None or agent.placed_on_machine_id != machine_id:
        return None
    if frame.base_revision != int(agent.instructions_revision or 0):
        current = await current_snapshot(db, agent)
        return {
            "type": "agent_instructions_ack",
            "agent_id": agent.id,
            "status": "rejected",
            "revision": int(agent.instructions_revision or 0),
            "digest": "",
            "snapshot": current.payload(),
        }
    try:
        revision = await record_snapshot(
            db,
            agent,
            Snapshot(frame.snapshot.agents_md, dict(frame.snapshot.skills)),
            source="agent",
        )
    except AgentFilePathError as exc:
        logger.warning("instructions.update_invalid", agent_id=agent.id, error=str(exc))
        await db.rollback()
        return None
    return {
        "type": "agent_instructions_ack",
        "agent_id": agent.id,
        "status": "accepted",
        "revision": revision,
        "digest": frame.digest,
    }


# ── Writes ──────────────────────────────────────────────────────────


def _apply_locally(snapshot: Snapshot, operation: Operation, path: str | None,
                   text: str | None, replacement: Snapshot | None) -> Snapshot:
    skills = dict(snapshot.skills)
    if operation == "write_agents_md":
        return Snapshot(_normalise(text), skills)
    if operation == "replace":
        assert replacement is not None
        return Snapshot(replacement.agents_md, dict(replacement.skills))
    assert path is not None
    if operation == "write_skill":
        skills[path] = text or ""
    else:
        skills.pop(path, None)
    return Snapshot(snapshot.agents_md, skills)


async def write_instructions(
    db: AsyncSession,
    bus: MachineBus,
    agent: Agent,
    *,
    operation: Operation,
    by_user_id: str | None,
    path: str | None = None,
    text: str | None = None,
    replacement: Snapshot | None = None,
    restored_from: int | None = None,
) -> int:
    """Change an agent's instructions and record the new revision.

    Commits. Raises ``HTTPException`` 409 while the agent's machine is
    offline or when the agent changed its instructions after the version
    the person was editing.
    """
    if path is not None:
        try:
            validate_agent_file_path(path)
        except AgentFilePathError as exc:
            raise HTTPException(400, f"invalid file path {path!r}: {exc}") from None
        if not is_instruction_path(path):
            raise HTTPException(400, "Only skills/ files are instructions")
    machine = (
        await db.get(Machine, agent.placed_on_machine_id)
        if agent.placed_on_machine_id
        else None
    )
    mode = instructions_mode(agent, machine, bus)
    if mode == "disk_offline":
        raise HTTPException(
            409,
            "The agent's machine is offline. Its instructions are read-only until it reconnects.",
        )
    if mode == "disk":
        assert machine is not None
        try:
            raw = await bus.request_instructions(
                machine.id,
                agent_id=agent.id,
                base_revision=int(agent.instructions_revision or 0),
                operation=operation,
                path=path,
                text=text,
                snapshot=replacement.payload() if replacement is not None else None,
            )
        except MachineRequestError as exc:
            raise HTTPException(409, f"The agent's machine did not answer ({exc.reason})") from None
        result = AgentInstructionsResultFrame.model_validate(raw)
        if result.status == "ok" and result.snapshot is not None:
            revision = await record_snapshot(
                db,
                agent,
                Snapshot(result.snapshot.agents_md, dict(result.snapshot.skills)),
                source="user",
                by_user_id=by_user_id,
                restored_from=restored_from,
            )
            await db.commit()
            await bus.send(machine.id, {
                "type": "agent_instructions_ack",
                "agent_id": agent.id,
                "status": "accepted",
                "revision": revision,
                "digest": result.digest or "",
            })
            return revision
        if result.status == "conflict":
            raise HTTPException(
                409,
                "The agent changed its instructions after this version. "
                "Reload them and try again.",
            )
        if result.status == "invalid":
            raise HTTPException(400, result.reason or "Invalid instructions change")
        if result.status == "error":
            raise HTTPException(409, result.reason or "The machine could not change the instructions")
        # ``no_receipt``: the machine has not seeded this agent yet, so the
        # snapshot is still the source; it seeds the disk on the next start.
    current = await current_snapshot(db, agent)
    revision = await record_snapshot(
        db,
        agent,
        _apply_locally(current, operation, path, text, replacement),
        source="user",
        by_user_id=by_user_id,
        restored_from=restored_from,
    )
    await db.commit()
    return revision


async def list_revisions(db: AsyncSession, agent_id: str) -> list[AgentInstructionRevision]:
    return list(
        (
            await db.scalars(
                select(AgentInstructionRevision)
                .where(AgentInstructionRevision.agent_id == agent_id)
                .order_by(AgentInstructionRevision.revision.desc())
            )
        ).all()
    )
