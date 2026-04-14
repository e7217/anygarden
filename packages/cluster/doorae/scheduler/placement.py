"""Bin-pack machine selection for agent placement."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Agent, Machine, MachineEngine
from doorae.scheduler.machine_bus import MachineBus


class NoSuitableMachineError(Exception):
    """Raised when no online machine can satisfy the placement request."""


async def select_machine_for(
    engine: str,
    db: AsyncSession,
    machine_bus: MachineBus,
    required_labels: dict | None = None,
) -> Machine:
    """Bin-pack: select online machine with fewest running agents that supports *engine*.

    Filter criteria:
    - Machine status = 'online'
    - Machine has an active WS connection (via *machine_bus*)
    - Machine supports the requested engine (via ``machine_engines``)
    - Machine has not reached ``max_agents``
    - Machine labels match *required_labels* (if specified)

    Sorting:
    - Fewest running agents first (bin-pack strategy)

    Raises :class:`NoSuitableMachineError` if no machine qualifies.
    """
    # Sub-query: count running agents per machine
    running_count = (
        select(
            Agent.placed_on_machine_id.label("machine_id"),
            func.count(Agent.id).label("running_count"),
        )
        .where(Agent.actual_state == "running")
        .group_by(Agent.placed_on_machine_id)
        .subquery()
    )

    # Main query: online machines supporting the engine
    stmt = (
        select(Machine, func.coalesce(running_count.c.running_count, 0).label("agent_count"))
        .join(MachineEngine, MachineEngine.machine_id == Machine.id)
        .outerjoin(running_count, running_count.c.machine_id == Machine.id)
        .where(
            Machine.status == "online",
            MachineEngine.engine == engine,
        )
        .order_by(func.coalesce(running_count.c.running_count, 0).asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    connected_ids = machine_bus.connected_ids()

    for row in rows:
        machine = row[0]
        agent_count = row[1]

        # Must have an active WS connection
        if machine.id not in connected_ids:
            continue

        # Must not exceed max_agents
        if agent_count >= machine.max_agents:
            continue

        # Label matching (if required)
        if required_labels:
            machine_labels = machine.labels or {}
            if not all(
                machine_labels.get(k) == v for k, v in required_labels.items()
            ):
                continue

        return machine

    raise NoSuitableMachineError(
        f"No suitable online machine found for engine={engine!r}"
    )
