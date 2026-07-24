"""WebSocket handler for machine daemon connections: ``/ws/machines/{machine_id}``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.machine_token import verify_machine_token_hash
from anygarden.config import AnygardenSettings
from anygarden.db.models import (
    Machine,
    MachineActivityLog,
    MachineEngine,
    MachineEngineStatus,
    MachineToken,
)
from anygarden.observability.metrics import machines_online
from anygarden.system.version_service import is_update_available

logger = structlog.get_logger(__name__)

router = APIRouter()


async def _authenticate_machine(
    db: AsyncSession,
    machine_id: str,
    raw_protocols: str,
) -> bool:
    """Validate machine token from ``Sec-WebSocket-Protocol: anygarden.v1, bearer.<token>``."""
    token: str | None = None
    for part in raw_protocols.split(","):
        part = part.strip()
        if part.startswith("bearer."):
            token = part[7:]
            break

    if token is None or not token.startswith("mch_"):
        return False

    # Use lookup hint (first 12 chars) to narrow candidates
    hint = token[:12]
    result = await db.execute(
        select(MachineToken).where(
            MachineToken.machine_id == machine_id,
            MachineToken.lookup_hint == hint,
            MachineToken.revoked_at.is_(None),
        )
    )
    candidates = result.scalars().all()

    for mt in candidates:
        # Check expiry
        if mt.expires_at and mt.expires_at < datetime.now(timezone.utc):
            continue
        if verify_machine_token_hash(token, mt.token_hash):
            return True

    return False


@router.websocket("/ws/machines/{machine_id}")
async def ws_machine(websocket: WebSocket, machine_id: str) -> None:
    """WebSocket endpoint for machine daemon communication."""
    app = websocket.app
    config: AnygardenSettings = app.state.config
    session_factory = app.state.session_factory
    machine_bus = app.state.machine_bus
    lifecycle = app.state.agent_lifecycle

    # ── Authentication ──
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    async with session_factory() as db:
        authed = await _authenticate_machine(db, machine_id, raw_protocols)
    if not authed:
        await websocket.close(code=4001, reason="Machine authentication failed")
        return

    selected_subprotocol: str | None = "anygarden.v1" if raw_protocols else None
    await websocket.accept(subprotocol=selected_subprotocol)

    # Register in bus
    await machine_bus.register(machine_id, websocket)
    machines_online.inc()  # #427 — revive the fleet-health gauge
    logger.info("machine_ws.connected", machine_id=machine_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue

            frame_type = data.get("type")

            if frame_type == "register":
                await _handle_register(session_factory, machine_id, data)
                # Place any orphaned agents (desired=running, no machine)
                await _place_orphaned_agents(session_factory, lifecycle)

            elif frame_type == "report_actual_state":
                agents_data = data.get("agents", [])
                await lifecycle.handle_report_actual_state(machine_id, agents_data)
                # Send sync_batch for reconciliation after every report
                await lifecycle.send_sync_batch(machine_id)

            elif frame_type == "token_request":
                agent_ids = data.get("agent_ids", [])
                grants = await lifecycle.handle_token_request(machine_id, agent_ids)
                for grant in grants:
                    await machine_bus.send(machine_id, grant)

            elif frame_type == "request_replacement":
                agent_id = data.get("agent_id", "")
                reason = data.get("reason", "")
                await lifecycle.handle_request_replacement(machine_id, agent_id, reason)

            elif frame_type == "self_update_result":
                # #550 — daemon reported self-update progress/outcome.
                await _handle_self_update_result(session_factory, machine_id, data)

            elif frame_type == "engine_check_result":
                # #553 — daemon reported an engine's current vs latest version.
                await _handle_engine_check_result(session_factory, machine_id, data)

            elif frame_type == "engine_update_result":
                # #553 — daemon reported engine update progress/outcome.
                await _handle_engine_update_result(session_factory, machine_id, data)

            elif frame_type == "agent_memory_update":
                # #237 — file → DB sync. Machine observed a change in
                # ``memory/notes.md`` and shipped the full body. We
                # overwrite the snapshot so the next spawn's
                # materialize-from-DB picks up the new content.
                agent_id = data.get("agent_id", "")
                memory_md = data.get("memory_md", "")
                if agent_id:
                    async with session_factory() as db:
                        from anygarden.db.models import Agent
                        from sqlalchemy import update

                        await db.execute(
                            update(Agent)
                            .where(Agent.id == agent_id)
                            .values(memory_md=memory_md)
                        )
                        await db.commit()

            elif frame_type == "room_artifact_produced":
                # #290 Phase B — agent dropped a file under
                # ``memory/outbox/`` and the daemon shipped it. Persist
                # to disk + DB and broadcast ``room_artifact.added`` to
                # live subscribers in every target room.
                from anygarden.rooms.artifacts import handle_artifact_produced
                from anygarden.ws.protocol import RoomArtifactAddedOut

                async with session_factory() as db:
                    inserted = await handle_artifact_produced(
                        db,
                        data,
                        artifact_files_dir=config.artifact_files_dir,
                    )
                connection_manager = getattr(
                    app.state, "connection_manager", None
                )
                if connection_manager is not None:
                    for row in inserted:
                        out = RoomArtifactAddedOut(
                            artifact={
                                "id": row.id,
                                "room_id": row.room_id,
                                "produced_by_agent_id": row.produced_by_agent_id,
                                "filename": row.filename,
                                "sha256": row.sha256,
                                "size_bytes": row.size_bytes,
                                "mime": row.mime,
                                "created_at": row.created_at.isoformat(),
                            }
                        )
                        await connection_manager.broadcast(row.room_id, out)

            else:
                logger.warning(
                    "machine_ws.unknown_frame",
                    machine_id=machine_id,
                    frame_type=frame_type,
                )

    except WebSocketDisconnect:
        logger.info("machine_ws.disconnected", machine_id=machine_id)
    except Exception as exc:
        logger.error("machine_ws.error", machine_id=machine_id, error=str(exc))
    finally:
        await machine_bus.unregister(machine_id)
        machines_online.dec()  # #427 — balance the connect increment
        # Mark machine offline on disconnect
        async with session_factory() as db:
            result = await db.execute(
                select(Machine).where(Machine.id == machine_id)
            )
            machine = result.scalar_one_or_none()
            if machine:
                machine.status = "offline"
                db.add(MachineActivityLog(
                    machine_id=machine_id,
                    event_type="offline",
                ))
                await db.commit()


def _apply_system_info(machine: Machine, system_info: Any) -> None:
    """Copy daemon-reported static system info onto ``machine`` (issue #523).

    Only meaningful values overwrite existing columns: an empty string / 0
    (a failed daemon-side probe) is ignored so it can't wipe a good value.
    ``lan_ip`` uses ``is not None`` since any non-null string is meaningful.
    """
    if not isinstance(system_info, dict):
        return
    hostname = system_info.get("hostname")
    if hostname:
        machine.hostname = str(hostname)
    lan_ip = system_info.get("lan_ip")
    if lan_ip is not None:
        machine.lan_ip = str(lan_ip)
    os_platform = system_info.get("os_platform")
    if os_platform:
        machine.os_platform = str(os_platform)
    cpu_cores = system_info.get("cpu_cores")
    if isinstance(cpu_cores, (int, float)) and cpu_cores:
        machine.cpu_cores = int(cpu_cores)
    memory_gb = system_info.get("memory_gb")
    if isinstance(memory_gb, (int, float)) and memory_gb:
        machine.memory_gb = float(memory_gb)


async def _handle_self_update_result(
    session_factory,
    machine_id: str,
    data: dict[str, Any],
) -> None:
    """Record a ``self_update_result`` from the daemon (#550).

    ``failed`` records the error and clears the pending state. ``updating``
    just confirms the daemon accepted the command — success is recognized
    separately when the daemon re-registers on the new version.
    """
    status = data.get("status")
    async with session_factory() as db:
        machine = await db.get(Machine, machine_id)
        if machine is None:
            return
        if status == "failed":
            machine.update_status = "failed"
            machine.update_error = data.get("error")
        elif status == "updating":
            machine.update_status = "updating"
            machine.update_error = None
        await db.commit()


async def _upsert_engine_status(
    db: AsyncSession, machine_id: str, engine: str
) -> MachineEngineStatus:
    """Fetch the (machine_id, engine) status row, creating it if absent.

    Lives in ``machine_engine_status`` (not ``machine_engines``) so it survives
    the register-time delete+recreate of the detection table (#553).
    """
    row = (await db.execute(
        select(MachineEngineStatus).where(
            MachineEngineStatus.machine_id == machine_id,
            MachineEngineStatus.engine == engine,
        )
    )).scalar_one_or_none()
    if row is None:
        row = MachineEngineStatus(machine_id=machine_id, engine=engine)
        db.add(row)
    return row


async def _handle_engine_check_result(
    session_factory,
    machine_id: str,
    data: dict[str, Any],
) -> None:
    """Record an ``engine_check_result``: latest version + availability (#553).

    Versions arrive channel-normalized from the daemon; the comparison reuses
    the #546 PEP 440 helper. A missing current version yields no availability
    signal (nothing to compare against).
    """
    engine = data.get("engine")
    if not engine:
        return
    current = data.get("current_version")
    latest = data.get("latest_version")
    async with session_factory() as db:
        machine = await db.get(Machine, machine_id)
        if machine is None:
            return
        status = await _upsert_engine_status(db, machine_id, engine)
        status.latest_version = latest
        status.latest_checked_at = datetime.now(timezone.utc)
        status.latest_error = data.get("error")
        status.update_available = (
            is_update_available(current, latest) if current else False
        )
        await db.commit()


async def _handle_engine_update_result(
    session_factory,
    machine_id: str,
    data: dict[str, Any],
) -> None:
    """Record an ``engine_update_result``: updating → success/failed (#553).

    On success the availability flag is cleared — the engine is now at latest;
    the next check will re-derive it.
    """
    engine = data.get("engine")
    result_status = data.get("status")
    if not engine:
        return
    async with session_factory() as db:
        machine = await db.get(Machine, machine_id)
        if machine is None:
            return
        status = await _upsert_engine_status(db, machine_id, engine)
        if result_status == "failed":
            status.update_status = "failed"
            status.update_error = data.get("error")
        elif result_status == "success":
            status.update_status = "success"
            status.update_error = None
            status.update_available = False
        elif result_status == "updating":
            status.update_status = "updating"
            status.update_error = None
        await db.commit()


async def _handle_register(
    session_factory,
    machine_id: str,
    data: dict[str, Any],
) -> None:
    """Process a ``register`` frame: save capabilities and mark online."""
    engines = data.get("capabilities", data.get("engines", []))
    daemon_version = data.get("daemon_version")

    async with session_factory() as db:
        result = await db.execute(
            select(Machine).where(Machine.id == machine_id)
        )
        machine = result.scalar_one_or_none()
        if machine is None:
            return

        machine.status = "online"
        machine.daemon_last_seen_at = datetime.now(timezone.utc)
        old_version = machine.daemon_version
        if daemon_version:
            machine.daemon_version = daemon_version

        # #550 — a pending server-driven self-update is confirmed successful
        # when the daemon comes back online reporting a different version.
        # (Only outdated machines are updated, so the version always changes
        # on a real success; a same-version reconnect stays "updating" and
        # is resolved out of band.)
        if (
            machine.update_status == "updating"
            and daemon_version
            and daemon_version != old_version
        ):
            machine.update_status = "success"
            machine.update_error = None

        # Static system info (issue #523). Only overwrite a field when the
        # daemon reported a meaningful value, so a partial collection failure
        # (empty hostname, 0 cpu_cores) never clobbers a previously-good value.
        _apply_system_info(machine, data.get("system_info"))

        # Replace engine list
        await db.execute(
            delete(MachineEngine).where(MachineEngine.machine_id == machine_id)
        )
        for eng in engines:
            name = eng if isinstance(eng, str) else eng.get("engine", eng)
            version = None if isinstance(eng, str) else eng.get("version")
            db.add(MachineEngine(
                machine_id=machine_id,
                engine=name,
                version=version,
            ))

        db.add(MachineActivityLog(
            machine_id=machine_id,
            event_type="online",
            details={"engines": engines},
        ))
        await db.commit()
    logger.info(
        "machine_ws.registered",
        machine_id=machine_id,
        engines=engines,
    )


async def _place_orphaned_agents(session_factory, lifecycle) -> None:
    """Place agents with desired=running but no machine assignment.

    Called after a machine registers — at that point there's at least one
    online machine available for placement.
    """
    from anygarden.db.models import Agent

    async with session_factory() as db:
        result = await db.execute(
            select(Agent).where(
                Agent.desired_state == "running",
                Agent.placed_on_machine_id.is_(None),
            )
        )
        orphans = result.scalars().all()
        orphan_ids = [a.id for a in orphans]

    if not orphan_ids:
        return

    logger.info("machine_ws.placing_orphaned_agents", count=len(orphan_ids))
    for aid in orphan_ids:
        await lifecycle.request_start(aid)
