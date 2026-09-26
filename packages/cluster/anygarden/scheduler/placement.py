"""Bin-pack machine selection for agent placement."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, Machine, MachineEngine
from anygarden.scheduler.execution import ExecutionBus


class NoSuitableMachineError(Exception):
    """Raised when no online machine can satisfy the placement request."""


# A committed dispatch is a reservation until it stops, even before a machine
# acknowledges it. Stopping processes also keep their slot until termination.
CAPACITY_STATES = ("pending", "starting", "running", "stopping")


async def select_machine_for(
    engine: str,
    db: AsyncSession,
    machine_bus: ExecutionBus,
    required_labels: dict | None = None,
    required_control_capabilities: set[str] | None = None,
    machine_id: str | None = None,
    exclude_agent_id: str | None = None,
) -> Machine:
    """Select an online machine with the fewest occupied or reserved slots.

    Filter criteria:
    - Machine status = 'online'
    - Machine has an active WS connection (via *machine_bus*)
    - Machine supports the requested engine (via ``machine_engines``)
    - Machine has not reached ``max_agents``
    - Machine labels match *required_labels* (if specified)
    - Machine matches an explicit initial placement (if specified)

    Sorting:
    - Fewest occupied or reserved slots first

    Raises :class:`NoSuitableMachineError` if no machine qualifies.
    """
    # Count both live processes and committed in-flight placements.
    running_count = (
        select(
            Agent.placed_on_machine_id.label("machine_id"),
            func.count(Agent.id).label("running_count"),
        )
        .where(Agent.actual_state.in_(CAPACITY_STATES))
        .where(Agent.id != exclude_agent_id if exclude_agent_id else True)
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
        .execution_options(populate_existing=True)
    )

    if machine_id is not None:
        stmt = stmt.where(Machine.id == machine_id)

    result = await db.execute(stmt)
    rows = result.all()

    connected_ids = machine_bus.connected_ids()

    for row in rows:
        machine = row[0]
        agent_count = row[1]

        # Must have an active WS connection
        if machine.id not in connected_ids:
            continue

        if required_control_capabilities and not required_control_capabilities.issubset(
            set(machine.control_capabilities or [])
        ):
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


async def reserve_machine_for(
    engine: str,
    db: AsyncSession,
    machine_bus: ExecutionBus,
    *,
    required_control_capabilities: set[str] | None = None,
    machine_id: str | None = None,
    exclude_agent_id: str | None = None,
) -> Machine:
    """Hold a database write lock until the caller commits its placement.

    Selection alone is a preflight check. Every writer that claims a new slot
    must use this function and persist the agent's pending placement in this
    same transaction. The no-op UPDATE is a row lock on PostgreSQL and a writer
    lock on SQLite. A separate SELECT after acquiring it sees the preceding
    holder's committed reservation instead of trusting an earlier count.
    """
    attempted: set[str] = set()
    while True:
        candidate = await select_machine_for(
            engine, db, machine_bus,
            required_control_capabilities=required_control_capabilities,
            machine_id=machine_id,
            exclude_agent_id=exclude_agent_id,
        )
        if candidate.id in attempted:
            raise NoSuitableMachineError("No machine capacity remains")
        attempted.add(candidate.id)
        await db.execute(
            update(Machine).where(Machine.id == candidate.id)
            .values(max_agents=Machine.max_agents)
            .execution_options(synchronize_session=False)
        )
        try:
            return await select_machine_for(
                engine, db, machine_bus,
                required_control_capabilities=required_control_capabilities,
                machine_id=candidate.id,
                exclude_agent_id=exclude_agent_id,
            )
        except NoSuitableMachineError:
            if machine_id is not None:
                raise
            # Another transaction claimed the last slot while we waited.
            # Re-run automatic selection, retaining acquired locks until the
            # caller finishes the transaction.
