"""Declarative desired-state agent lifecycle.

The server tells each machine what agents it *should* run
(``sync_desired_state`` / ``sync_batch``).  The machine autonomously
converges toward that desired state by spawning, killing, or restarting
processes.  It reports back with ``report_actual_state`` and may request
tokens or replacement placement as needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, case, event, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from anygarden.agent_availability import (
    CRASHED,
    ENGINE_MISMATCH,
    NO_MACHINE_FOR_ENGINE,
    NO_ROOM,
    SPAWN_FAILED,
)
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentFile,
    AgentSkill,
    AgentToken,
    AgentTurn,
    AgentTurnAttempt,
    Machine,
    Participant,
    Room,
    SkillLibraryEntry,
    WorkspaceAttachment,
)
from anygarden.scheduler.gateway_secrets import build_engine_secrets
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.scheduler.placement import NoSuitableMachineError, select_machine_for

logger = structlog.get_logger(__name__)

GENERATION_REPORT_CAPABILITY = "agent_generation_reports_v1"
LIFECYCLE_LEASE_SEC_DEFAULT = 120
_RETRYABLE_DELIVERY_STATES = frozenset({"dispatching", "pending_ack", "unknown"})


def _lifecycle_lease_expiry(now: datetime) -> datetime:
    """Return the bounded cross-worker lifecycle ownership deadline."""

    try:
        seconds = max(1, int(os.environ.get("ANYGARDEN_LIFECYCLE_LEASE_SEC", "120")))
    except ValueError:
        seconds = LIFECYCLE_LEASE_SEC_DEFAULT
    return now + timedelta(seconds=seconds)


def _manifest_hash(frame: dict, *, agent: Agent | None = None) -> str:
    """Return a stable effective-runtime hash without process credentials.

    Most restart inputs are carried in the machine manifest itself. The
    context-window opt-out is delivered in the agent's welcome frame after a
    reconnect, so include it as an internal hash-only input without expanding
    the machine protocol.
    """

    stable = dict(frame)
    stable.pop("generation", None)
    stable.pop("anygarden_mcp_token", None)
    if agent is not None:
        stable["_context_window_opt_out"] = bool(agent.context_window_opt_out)
    body = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _mark_unavailable(agent: Agent, code: str, detail: dict | None = None) -> None:
    """Stamp the structured unavailability reason (#516).

    ``unavailable_since`` is preserved when the code is unchanged so it
    reflects when *this* reason began rather than the latest report.
    """
    if agent.unavailable_code != code:
        agent.unavailable_since = datetime.now(timezone.utc)
    agent.unavailable_code = code
    agent.unavailable_detail = detail


def _clear_unavailable(agent: Agent) -> None:
    """Clear the unavailability reason — the agent can respond again (#516)."""
    agent.unavailable_code = None
    agent.unavailable_detail = None
    agent.unavailable_since = None


class AgentLifecycle:
    """Declarative lifecycle: the server pushes *desired* state to machines
    and reacts to state reports from them."""

    def __init__(
        self,
        db_factory,
        machine_bus: MachineBus,
        *,
        mcp_template_service=None,
        room_files_dir: Path | None = None,
        cluster_external_url: str | None = None,
        llm_gateway_enabled: bool = False,
    ) -> None:
        self._db_factory = db_factory
        self._machine_bus = machine_bus
        # #124 — Optional MCP template service. Kept optional so
        # tests that only care about the skill library path don't
        # need to wire a secrets key. When None, ``_build_sync_frame``
        # skips the MCP overlay step entirely (no-op for agents that
        # have no instances attached anyway).
        self._mcp_template_service = mcp_template_service
        # #255 — On-disk root for room shared files. Populated by the
        # app factory from ``AnygardenSettings.room_files_dir``; tests
        # that don't exercise shared-files behaviour leave it None and
        # the backfill hook becomes a no-op. Must match whatever the
        # ``/rooms/{id}/files`` HTTP route writes into, or the daemon
        # will receive stale bytes.
        self._room_files_dir = room_files_dir
        # #277 — URL agents target for cluster-side MCP and REST
        # calls. Stored here so ``_build_sync_frame`` can render the
        # built-in anygarden self-MCP entry without re-resolving settings
        # on every call. ``None`` skips self-MCP injection (used by
        # tests that don't exercise spawn-time MCP wiring).
        self._cluster_external_url = cluster_external_url
        # Issue #359 — gateway feature flag piped through so
        # ``_build_sync_frame`` can decide whether to populate
        # ``engine_secrets`` for openhands agents. Default ``False``
        # keeps pre-#359 tests + wiring source-compatible: the
        # behaviour they expected (``engine_secrets={}`` always) is
        # exactly what an off-flag still produces.
        self._llm_gateway_enabled = llm_gateway_enabled
        # Issue #369 — per-agent ``anygarden_token`` cache. Without this,
        # every ``_build_sync_frame`` invocation (which fires on
        # ``request_start``, ``handle_report_actual_state``,
        # broadcast snapshots, sync_batch ticks) mints a *new* token
        # and stages a new ``agent_tokens`` row. Whether the row
        # commits depends on the caller — read-only contexts
        # (broadcast / sync rebuild) leave the staged row uncommitted
        # while the manifest_store cache *does* update with the new
        # token regardless. The agent process reads its
        # ``OPENAI_API_KEY`` from stdin once at spawn; if it lands on
        # a token whose row never committed, every subsequent
        # gateway request 401s with 'Invalid agent token'.
        #
        # Cache contract:
        # - Key: agent_id; value: plaintext anygarden_token string.
        # - First mint per agent goes through ``request_start`` (the
        #   only path that's guaranteed to commit) and lands in the
        #   cache.
        # - Subsequent ``_build_sync_frame`` calls reuse the cached
        #   value — same token reaches the disk-baked .mcp.json,
        #   the engine_secrets stdin payload, and the
        #   manifest_store cache, so the three stay coherent.
        # - ``request_stop`` and ``delete_agent`` evict so the next
        #   start cycle issues a fresh token (rotation on respawn).
        self._token_cache: dict[str, str] = {}
        # Request-level in-process dedupe for ``request_start``. This
        # prevents concurrent manual/API-triggered start calls from
        # sending duplicate sync frames for the same agent within this
        # process.
        self._start_inflight: set[str] = set()

    async def request_start(self, agent_id: str) -> None:
        """Claim durable lifecycle ownership and dispatch desired state.

        The database lease is the cross-worker authority. ``_start_inflight``
        remains only a same-process fast path; it is never relied on for
        correctness. A websocket ``False`` result is treated as unknown
        delivery, so the committed placement/generation stay intact and a
        later owner may only retry the same generation on the same machine.
        """
        # Issue #369 — invalidate any cached anygarden_token on each
        # explicit start. The new spawn frame mints a fresh token and
        # re-populates the cache; subsequent rebuilds reuse it. Without
        # this evict, a stop → start cycle would reuse the previous
        # spawn's plaintext, which is undesirable both for security
        # rotation and for race scenarios where the prior process
        # outlives the stop window.
        if agent_id in self._start_inflight:
            logger.info(
                "lifecycle.start_skipped_inflight",
                agent_id=agent_id,
                reason="duplicate_call_in_progress",
            )
            return

        self._start_inflight.add(agent_id)
        try:
            ownership_token = secrets.token_urlsafe(32)
            generation: int | None = None
            machine_id: str | None = None
            frame: dict | None = None

            async with self._db_factory() as db:
                agent = await self._get_agent(db, agent_id)
                if agent is None:
                    logger.error("lifecycle.agent_not_found", agent_id=agent_id)
                    return

                now = datetime.now(timezone.utc)
                lease_active = bool(
                    agent.lifecycle_lease_token
                    and agent.lifecycle_lease_expires_at
                    and agent.lifecycle_lease_expires_at > now
                )
                if lease_active:
                    logger.info(
                        "lifecycle.start_skipped_inflight",
                        agent_id=agent_id,
                        reason="durable_lease_active",
                        existing_machine_id=agent.placed_on_machine_id,
                    )
                    return

                if (
                    agent.desired_state == "running"
                    and agent.actual_state == "running"
                    and agent.placed_on_machine_id
                ):
                    logger.info(
                        "lifecycle.start_skipped_inflight",
                        agent_id=agent_id,
                        reason="already_running",
                        existing_machine_id=agent.placed_on_machine_id,
                    )
                    return

                # Refuse to dispatch if agent has no room memberships.
                result = await db.execute(
                    select(Participant.room_id).where(Participant.agent_id == agent.id)
                )
                rooms = [row[0] for row in result.all()]
                if not rooms:
                    logger.warning(
                        "lifecycle.spawn_refused_no_rooms", agent_id=agent_id
                    )
                    agent.actual_state = "pending"
                    agent.desired_state = "running"
                    agent.placed_on_machine_id = None
                    agent.pid = None
                    agent.lifecycle_lease_token = None
                    agent.lifecycle_lease_expires_at = None
                    agent.lifecycle_delivery_state = "released"
                    agent.last_crash_reason = (
                        "no rooms assigned \u2014 add the agent to at least one room "
                        "before starting"
                    )
                    _mark_unavailable(agent, NO_ROOM, None)
                    await db.commit()
                    return

                retry_same_generation = bool(
                    agent.placed_on_machine_id
                    and agent.desired_state == "running"
                    and agent.actual_state in ("starting", "pending")
                    and (
                        agent.lifecycle_delivery_state in _RETRYABLE_DELIVERY_STATES
                        or agent.lifecycle_delivery_state is None
                    )
                )

                machine: Machine | None
                if agent.placed_on_machine_id:
                    # Unknown delivery is never permission to choose a second
                    # machine. Reconciliation stays on the committed placement
                    # until that machine reports the matching generation.
                    machine = await db.get(Machine, agent.placed_on_machine_id)
                    if machine is None or not self._machine_bus.is_connected(
                        machine.id
                    ):
                        logger.info(
                            "lifecycle.start_waiting_for_placement",
                            agent_id=agent_id,
                            machine_id=agent.placed_on_machine_id,
                            reason="same_placement_not_connected",
                        )
                        return
                else:
                    try:
                        machine = await select_machine_for(
                            agent.engine, db, self._machine_bus
                        )
                    except NoSuitableMachineError:
                        machine = None

                if machine is None:
                    logger.warning(
                        "lifecycle.no_machine",
                        agent_id=agent_id,
                        engine=agent.engine,
                    )
                    agent.actual_state = "pending"
                    # #516 — previously the only *silent* start failure: no reason,
                    # no ActivityLog, and the stale placement stranded the agent
                    # from ``_place_orphaned_agents`` (which filters
                    # ``placed_on_machine_id IS NULL``). Now record the reason,
                    # release the placement so a newly-registered machine can adopt
                    # it, and leave an audit trail.
                    agent.placed_on_machine_id = None
                    agent.lifecycle_lease_token = None
                    agent.lifecycle_lease_expires_at = None
                    agent.lifecycle_delivery_state = "released"
                    agent.last_crash_reason = (
                        f"no online machine supports engine '{agent.engine}'"
                    )
                    _mark_unavailable(
                        agent, NO_MACHINE_FOR_ENGINE, {"engine": agent.engine}
                    )
                    db.add(
                        ActivityLog(
                            agent_id=agent_id,
                            event_type="agent_unavailable",
                            details={
                                "code": NO_MACHINE_FOR_ENGINE,
                                "engine": agent.engine,
                            },
                        )
                    )
                    await db.commit()
                    return

                old_generation = int(agent.generation or 0)
                expected_placement = agent.placed_on_machine_id
                lease_available = or_(
                    Agent.lifecycle_lease_token.is_(None),
                    Agent.lifecycle_lease_expires_at.is_(None),
                    Agent.lifecycle_lease_expires_at <= now,
                )
                claim_values: dict = {
                    "lifecycle_lease_token": ownership_token,
                    "lifecycle_lease_expires_at": _lifecycle_lease_expiry(now),
                    "lifecycle_delivery_state": "dispatching",
                }
                if retry_same_generation:
                    claimed_generation = old_generation
                else:
                    claimed_generation = old_generation + 1
                    claim_values.update(
                        {
                            "placed_on_machine_id": machine.id,
                            "desired_state": "running",
                            "actual_state": "pending",
                            "generation": claimed_generation,
                            "started_at": now,
                            "pending_generation": None,
                            "restart_requested_at": None,
                            "restart_deadline_at": None,
                            "pending_manifest_hash": None,
                        }
                    )
                    capabilities = set(machine.control_capabilities or [])
                    if (
                        GENERATION_REPORT_CAPABILITY not in capabilities
                        and agent.legacy_report_generation is None
                    ):
                        claim_values["legacy_report_generation"] = claimed_generation

                claim = await db.execute(
                    update(Agent)
                    .where(
                        Agent.id == agent_id,
                        Agent.generation == old_generation,
                        Agent.placed_on_machine_id == expected_placement,
                        lease_available,
                    )
                    .values(**claim_values)
                    .execution_options(synchronize_session=False)
                )
                if claim.rowcount != 1:
                    await db.rollback()
                    logger.info(
                        "lifecycle.start_skipped_inflight",
                        agent_id=agent_id,
                        reason="durable_claim_lost",
                    )
                    return

                await db.commit()
                await db.refresh(agent)
                generation = claimed_generation
                machine_id = machine.id

                # Only the durable claim winner rotates/builds process
                # credentials. Cross-worker losers never mint or dispatch.
                self._token_cache.pop(agent_id, None)
                # A machine was found — the prior no_machine / no_room reason (if
                # any) no longer applies. A later spawn failure re-stamps a fresh
                # reason via ``handle_report_actual_state``.
                if not retry_same_generation:
                    _clear_unavailable(agent)

                frame = await self._build_sync_frame(db, agent, rooms)
                agent.manifest_hash = _manifest_hash(frame, agent=agent)
                db.add(
                    ActivityLog(
                        agent_id=agent_id,
                        event_type=(
                            "start_reconciled"
                            if retry_same_generation
                            else "start_requested"
                        ),
                        details={
                            "machine_id": machine.id,
                            "generation": claimed_generation,
                            "delivery_state": "dispatching",
                        },
                    )
                )
                await db.commit()

            assert (
                frame is not None and generation is not None and machine_id is not None
            )
            if not await self._dispatch_claim_is_current(
                agent_id=agent_id,
                generation=generation,
                machine_id=machine_id,
                ownership_token=ownership_token,
            ):
                return
            sent = await self._machine_bus.send(machine_id, frame)
            await self._record_dispatch_result(
                agent_id=agent_id,
                generation=generation,
                machine_id=machine_id,
                ownership_token=ownership_token,
                sent=sent,
            )
        finally:
            self._start_inflight.discard(agent_id)

    async def _dispatch_claim_is_current(
        self,
        *,
        agent_id: str,
        generation: int,
        machine_id: str,
        ownership_token: str,
    ) -> bool:
        """Fresh-read the durable authority immediately before dispatch."""

        async with self._db_factory() as db:
            current = await db.scalar(
                select(func.count())
                .select_from(Agent)
                .where(
                    Agent.id == agent_id,
                    Agent.generation == generation,
                    Agent.placed_on_machine_id == machine_id,
                    Agent.desired_state == "running",
                    Agent.lifecycle_lease_token == ownership_token,
                    Agent.lifecycle_delivery_state == "dispatching",
                )
            )
            return bool(current)

    async def _record_dispatch_result(
        self,
        *,
        agent_id: str,
        generation: int,
        machine_id: str,
        ownership_token: str,
        sent: bool,
    ) -> bool:
        """CAS the result without letting a stale sender erase newer state."""

        state = "pending_ack" if sent else "unknown"
        async with self._db_factory() as db:
            result = await db.execute(
                update(Agent)
                .where(
                    Agent.id == agent_id,
                    Agent.generation == generation,
                    Agent.placed_on_machine_id == machine_id,
                    Agent.desired_state == "running",
                    Agent.lifecycle_lease_token == ownership_token,
                    Agent.lifecycle_delivery_state == "dispatching",
                )
                .values(lifecycle_delivery_state=state)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                await db.rollback()
                logger.info(
                    "lifecycle.dispatch_result_fenced",
                    agent_id=agent_id,
                    machine_id=machine_id,
                    generation=generation,
                )
                return False

            if not sent:
                logger.warning(
                    "lifecycle.sync_delivery_unknown",
                    agent_id=agent_id,
                    machine_id=machine_id,
                    generation=generation,
                )
                agent = await self._get_agent(db, agent_id)
                assert agent is not None
                agent.last_crash_reason = (
                    f"dispatch_unknown to {machine_id}: awaiting machine reconciliation"
                )
                _mark_unavailable(
                    agent,
                    SPAWN_FAILED,
                    {"machine_id": machine_id, "reason": "delivery_unknown"},
                )
                db.add(
                    ActivityLog(
                        agent_id=agent_id,
                        event_type="lifecycle_dispatch_unknown",
                        details={
                            "machine_id": machine_id,
                            "generation": generation,
                            "recovery": "same_placement_reconcile",
                        },
                    )
                )
            await db.commit()
            return True

    def _acquire_anygarden_token(self, db: AsyncSession, agent_id: str) -> str:
        """Return the per-agent ``anygarden_token``, minting one on cache miss.

        Issue #369 — single mint point for the anygarden self-MCP /
        gateway-auth bearer. Cache hit returns the previously-minted
        plaintext (already committed via ``request_start``); miss
        mints a fresh token, stages an ``agent_tokens`` row via
        ``db.add``, and stores the plaintext in the cache.

        The mint side-effect (``db.add``) only takes effect when the
        caller commits its transaction. ``request_start`` always
        commits; ``_build_sync_frame`` invocations from broadcast /
        rebuild paths do not, but those paths hit the cache instead
        so they don't trigger a mint at all. Net result: one row per
        agent per active spawn, with the plaintext stable until
        ``request_stop`` evicts.

        Issue #445 — the durable cache is populated *only after* the
        staged ``agent_tokens`` row commits. Caching eagerly (pre-#445)
        meant a caller that never committed (``send_sync_batch``) or
        one whose surrounding transaction rolled back left the
        plaintext in the cache pointing at a row that was never
        persisted. After a restart the cache is empty but the agent
        already holds that stdin-piped token, so every gateway/MCP call
        401s in a storm.

        Two-stage cache to satisfy both invariants:
        - *Pending* mints are stashed per-session (``Session.info``) so
          repeated ``_build_sync_frame`` calls inside the *same*
          uncommitted transaction reuse one token / one row (#369).
        - On ``after_commit`` those pending tokens graduate into the
          durable ``_token_cache``. A rollback — or a session that
          exits without committing — never fires the listener, so the
          durable cache mirrors the committed DB state (#445).
        """
        cached = self._token_cache.get(agent_id)
        if cached is not None:
            return cached

        sync_session = db.sync_session
        # Per-session scratch for tokens minted-but-not-yet-committed.
        pending: dict[str, str] = sync_session.info.setdefault(
            "_pending_anygarden_tokens", {}
        )
        pending_token = pending.get(agent_id)
        if pending_token is not None:
            # Same transaction, second acquire — reuse so we don't
            # stage a duplicate ``agent_tokens`` row (#369 invariant).
            return pending_token

        token = generate_token()
        token_hash, lookup_hint = hash_agent_token(token)
        db.add(
            AgentToken(
                agent_id=agent_id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            )
        )
        pending[agent_id] = token

        if not sync_session.info.get("_anygarden_token_commit_hook"):
            # Arm the promote-on-commit hook once per session. ``once``
            # auto-removes it safely after it fires (no mid-dispatch
            # deque mutation). The hook drains *all* pending tokens for
            # the session, so a single listener covers multiple agents
            # batched into one transaction (``send_sync_batch`` /
            # ``handle_token_request`` shapes).
            sync_session.info["_anygarden_token_commit_hook"] = True

            def _promote_pending_on_commit(session) -> None:
                staged = session.info.pop("_pending_anygarden_tokens", {})
                session.info.pop("_anygarden_token_commit_hook", None)
                for aid, tok in staged.items():
                    self._token_cache[aid] = tok

            event.listen(
                sync_session,
                "after_commit",
                _promote_pending_on_commit,
                once=True,
            )
        return token

    def evict_token(self, agent_id: str) -> None:
        """Drop the cached ``anygarden_token`` for ``agent_id`` (#369).

        Public hook so non-lifecycle paths (admin DELETE on
        ``/api/v1/agents/{id}``) can clear the cache without reaching
        into ``_token_cache`` directly. No-op when the agent has no
        cached token, so callers don't need a guard.
        """
        self._token_cache.pop(agent_id, None)

    async def request_stop(self, agent_id: str) -> None:
        """Advance a stop fence and push ``sync_desired_state``.

        #219 — also flips ``actual_state`` into the transitional
        ``stopping`` badge immediately so admin UIs don't wait up to
        30 s for the machine's next periodic report. If the agent has
        no machine placement the daemon can't confirm the stop, so
        short-circuit to ``stopped`` here to avoid a permanent
        stuck-stopping row.
        """
        # Issue #369 — evict the cached anygarden_token so the next
        # ``request_start`` mints a fresh one (rotation on respawn).
        self._token_cache.pop(agent_id, None)
        machine_id: str | None = None
        stop_generation: int | None = None
        async with self._db_factory() as db:
            # Stop is a monotonic generation transition, not only a desired
            # state bit. A running frame that already passed the sender's
            # pre-dispatch check remains generation N; this atomic UPDATE
            # moves the durable stop tombstone to N+1 before its frame is
            # emitted, so the daemon can reject any delayed lower generation.
            stopped = await db.execute(
                update(Agent)
                .where(Agent.id == agent_id)
                .values(
                    desired_state="stopped",
                    actual_state=case(
                        (Agent.placed_on_machine_id.is_(None), "stopped"),
                        (
                            Agent.actual_state.in_(
                                ("running", "starting", "pending")
                            ),
                            "stopping",
                        ),
                        else_=Agent.actual_state,
                    ),
                    pid=case(
                        (Agent.placed_on_machine_id.is_(None), None),
                        else_=Agent.pid,
                    ),
                    generation=func.coalesce(Agent.generation, 0) + 1,
                    lifecycle_lease_token=None,
                    lifecycle_lease_expires_at=None,
                    lifecycle_delivery_state="stopped",
                    pending_generation=None,
                    restart_requested_at=None,
                    restart_deadline_at=None,
                    pending_manifest_hash=None,
                    unavailable_code=None,
                    unavailable_detail=None,
                    unavailable_since=None,
                )
                .returning(Agent.generation, Agent.placed_on_machine_id)
                .execution_options(synchronize_session=False)
            )
            row = stopped.first()
            if row is None:
                await db.rollback()
                return
            stop_generation = int(row[0])
            machine_id = row[1]
            db.add(
                ActivityLog(
                    agent_id=agent_id,
                    event_type="stop_requested",
                    details={"generation": stop_generation},
                )
            )
            await db.commit()

        if machine_id is not None and stop_generation is not None:
            await self._machine_bus.send(
                machine_id,
                {
                    "type": "sync_desired_state",
                    "agent_id": agent_id,
                    "desired_state": "stopped",
                    "generation": stop_generation,
                },
            )

    async def handle_report_actual_state(
        self,
        machine_id: str,
        agents_data: list[dict],
    ) -> None:
        """Update DB from machine's ``report_actual_state`` frame.

        Each dict in *agents_data* must contain at minimum ``agent_id``
        and ``actual_state``.  Optional keys: ``pid``, ``last_crash_reason``.
        """
        # #255 / #345 — Agents that just transitioned into ``running``
        # need their room-shared files re-pushed. Materialize now
        # preserves ``<agent_root>/memory/shared/`` when present, but a
        # fresh host or a cleaned agent directory still needs the
        # authoritative room files fanned out after spawn. Collected
        # here and flushed after the DB commit below; a heartbeat that
        # merely confirms an already-running agent stays no-op because
        # ``old_state`` is read before we overwrite it.
        backfill_targets: list[str] = []
        reported_ids = {
            entry.get("agent_id")
            for entry in agents_data
            if entry.get("agent_id") is not None
        }
        async with self._db_factory() as db:
            machine = await db.get(Machine, machine_id)
            generation_reports_required = bool(
                machine
                and GENERATION_REPORT_CAPABILITY
                in set(machine.control_capabilities or [])
            )
            reported_agents_by_id: dict[str, Agent] = {}
            if reported_ids:
                reported_agents = (
                    (await db.execute(select(Agent).where(Agent.id.in_(reported_ids))))
                    .scalars()
                    .all()
                )
                reported_agents_by_id = {agent.id: agent for agent in reported_agents}

            for entry in agents_data:
                aid = entry.get("agent_id")
                if not aid:
                    continue
                agent = reported_agents_by_id.get(aid)
                if agent is None:
                    continue
                # Only accept reports from the machine the agent is placed on.
                if agent.placed_on_machine_id != machine_id:
                    logger.warning(
                        "lifecycle.report_wrong_machine",
                        agent_id=aid,
                        expected=agent.placed_on_machine_id,
                        got=machine_id,
                    )
                    continue

                new_state = entry.get("actual_state")
                reported_generation = entry.get("generation")
                if reported_generation is None:
                    if generation_reports_required:
                        logger.warning(
                            "lifecycle.report_missing_generation",
                            agent_id=aid,
                            machine_id=machine_id,
                            expected=agent.generation,
                            reason="capability_requires_generation",
                        )
                        continue
                    if agent.legacy_report_generation is None:
                        # Compatibility bootstrap for rows created directly by
                        # older deployments/tests. Migration 063 pre-populates
                        # this epoch for every already-placed production row.
                        agent.legacy_report_generation = int(agent.generation or 0)
                    elif agent.legacy_report_generation != agent.generation:
                        logger.warning(
                            "lifecycle.report_stale_legacy_epoch",
                            agent_id=aid,
                            machine_id=machine_id,
                            expected=agent.generation,
                            allowed_legacy_generation=agent.legacy_report_generation,
                            reported_state=new_state,
                        )
                        continue
                if (
                    reported_generation is not None
                    and reported_generation != agent.generation
                ):
                    # A previous process generation can report after a
                    # restart has already advanced the desired manifest. Do
                    # not let that late frame overwrite the current state.
                    # Older daemons omit the key entirely and retain the
                    # pre-generation compatibility path.
                    logger.warning(
                        "lifecycle.report_stale_generation",
                        agent_id=aid,
                        expected=agent.generation,
                        got=reported_generation,
                        reported_state=new_state,
                    )
                    continue

                # A report from the committed machine and generation is the
                # durable acknowledgement for a running desired-state
                # dispatch. Stop already revoked the start owner; a late
                # pre-stop report must not relabel that terminal ownership.
                if agent.desired_state == "running":
                    agent.lifecycle_lease_token = None
                    agent.lifecycle_lease_expires_at = None
                    agent.lifecycle_delivery_state = "acknowledged"

                if agent.desired_state == "stopped" and new_state in (
                    "running",
                    "starting",
                ):
                    # request_stop commits ``stopping`` before dispatching
                    # the kill frame. A periodic report already in flight can
                    # still say running/starting for the same generation; it
                    # is an observation of the pre-stop world, not a reason
                    # to reverse the requested transition.
                    logger.info(
                        "lifecycle.report_ignored_during_stop",
                        agent_id=aid,
                        reported_state=new_state,
                    )
                    continue

                if agent.desired_state == "stopped" and new_state == "crashed":
                    # The process is gone while an intentional stop is in
                    # progress. Normalize this to the requested terminal
                    # state so admin surfaces do not raise a crash warning for
                    # a successful stop race.
                    new_state = "stopped"
                old_state = agent.actual_state
                if new_state:
                    agent.actual_state = new_state
                if "pid" in entry:
                    agent.pid = entry["pid"]
                if "last_crash_reason" in entry:
                    agent.last_crash_reason = entry["last_crash_reason"]
                if new_state == "running":
                    agent.last_heartbeat_at = datetime.now(timezone.utc)

                # #516 — maintain the structured unavailability reason from the
                # report. The live process carries its own ``engine`` so engine
                # drift (DB migrated the engine but the process is still the old
                # one) is detectable for free.
                if new_state == "running":
                    reported_engine = (entry.get("engine") or "").strip()
                    if reported_engine and reported_engine != agent.engine:
                        _mark_unavailable(
                            agent,
                            ENGINE_MISMATCH,
                            {
                                "db_engine": agent.engine,
                                "running_engine": reported_engine,
                            },
                        )
                    else:
                        _clear_unavailable(agent)
                elif new_state == "crashed":
                    # uptime≈0 ⇒ it never really started (spawn failure);
                    # otherwise it ran and then died.
                    uptime = entry.get("uptime_seconds") or 0
                    code = SPAWN_FAILED if uptime <= 0 else CRASHED
                    _mark_unavailable(
                        agent,
                        code,
                        {
                            "engine": agent.engine,
                            "stderr_tail": entry.get("last_crash_reason"),
                        },
                    )
                elif new_state == "stopped":
                    agent.lifecycle_lease_token = None
                    agent.lifecycle_lease_expires_at = None
                    agent.lifecycle_delivery_state = "stopped"
                    _clear_unavailable(agent)

                # Only log when state actually changed (skip heartbeat noise)
                if new_state and new_state != old_state:
                    db.add(
                        ActivityLog(
                            agent_id=aid,
                            event_type="state_changed",
                            details={
                                "from": old_state,
                                "to": new_state,
                                "pid": entry.get("pid"),
                                "machine_id": machine_id,
                            },
                        )
                    )
                    if new_state == "running":
                        backfill_targets.append(aid)
            # Agents placed on this machine but absent from the report:
            # if desired=stopped they are confirmed stopped. Keep
            # placed_on_machine_id so the machine page still lists them.
            where_conditions = [
                Agent.placed_on_machine_id == machine_id,
                Agent.desired_state == "stopped",
                Agent.actual_state != "stopped",
            ]
            if reported_ids:
                where_conditions.append(not_(Agent.id.in_(reported_ids)))
            placed_on_machine = (
                (await db.execute(select(Agent).where(and_(*where_conditions))))
                .scalars()
                .all()
            )
            for agent in placed_on_machine:
                old = agent.actual_state
                agent.actual_state = "stopped"
                agent.pid = None
                agent.lifecycle_lease_token = None
                agent.lifecycle_lease_expires_at = None
                agent.lifecycle_delivery_state = "stopped"
                _clear_unavailable(agent)  # #516 — confirmed stop, not a problem
                db.add(
                    ActivityLog(
                        agent_id=agent.id,
                        event_type="state_changed",
                        details={
                            "from": old,
                            "to": "stopped",
                            "machine_id": machine_id,
                            "reason": "absent_from_report",
                        },
                    )
                )

            await db.commit()

        # #255 — Flush shared-file backfill for newly-running agents.
        # Runs outside the state-update transaction so a backfill
        # failure can't roll back the actual_state commit. The daemon
        # compares ``content_sha256`` per file and skips rewrites, so
        # occasional double-sends (e.g. pending→starting→running
        # transitions arriving in separate frames) are harmless.
        if self._room_files_dir is not None and backfill_targets:
            await self._backfill_shared_files_for_agents(backfill_targets)

    async def _backfill_shared_files_for_agents(self, agent_ids: list[str]) -> None:
        """Push every room shared file to each agent in ``agent_ids``.

        #255 — Invoked after an agent transitions into ``running``,
        because the spawner pruned ``memory/shared/`` during spawn.
        Uses ``shared_files.backfill_agent`` which internally reads
        from the DB + ``room_files_dir`` and fans out via
        ``machine_bus`` — we just need to walk the agent's rooms.

        Failures are logged and swallowed per (agent, room) pair so a
        single missing-on-disk file doesn't starve the rest of the
        fleet of its backfill.
        """
        from anygarden.rooms import shared_files as shared_files_service

        async with self._db_factory() as db:
            room_map: dict[str, list[str]] = defaultdict(list)
            if agent_ids:
                room_rows = await db.execute(
                    select(Participant.agent_id, Participant.room_id).where(
                        Participant.agent_id.in_(agent_ids)
                    )
                )
                for row in room_rows:
                    room_map[row[0]].append(row[1])

            for aid in agent_ids:
                rooms = room_map.get(aid, [])
                for room_id in rooms:
                    try:
                        await shared_files_service.backfill_agent(
                            db,
                            machine_bus=self._machine_bus,
                            room_files_dir=self._room_files_dir,
                            room_id=room_id,
                            agent_id=aid,
                        )
                    except Exception as exc:
                        logger.warning(
                            "lifecycle.shared_files_backfill_failed",
                            agent_id=aid,
                            room_id=room_id,
                            error=str(exc),
                        )

    async def handle_token_request(
        self,
        machine_id: str,
        agent_ids: list[str],
    ) -> list[dict]:
        """Issue fresh tokens for the requested agents.

        Returns a list of ``{"agent_id": ..., "token": ...}`` dicts.
        Only agents actually placed on *machine_id* receive a token.
        """
        grants: list[dict] = []
        async with self._db_factory() as db:
            for aid in agent_ids:
                agent = await self._get_agent(db, aid)
                if agent is None:
                    logger.warning("lifecycle.token_req_not_found", agent_id=aid)
                    continue
                if agent.placed_on_machine_id != machine_id:
                    logger.warning(
                        "lifecycle.token_req_wrong_machine",
                        agent_id=aid,
                        expected=agent.placed_on_machine_id,
                        got=machine_id,
                    )
                    continue

                plain = generate_token()
                token_hash, lookup_hint = hash_agent_token(plain)
                db.add(
                    AgentToken(
                        agent_id=agent.id,
                        token_hash=token_hash,
                        lookup_hint=lookup_hint,
                    )
                )
                grants.append(
                    {
                        "type": "token_grant",
                        "agent_id": agent.id,
                        "agent_token": plain,
                    }
                )
            await db.commit()
        return grants

    async def handle_request_replacement(
        self,
        machine_id: str,
        agent_id: str,
        reason: str,
        generation: int | None = None,
    ) -> None:
        """Machine relinquishes one fenced generation before replacement."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return
            machine = await db.get(Machine, machine_id)
            requires_generation = bool(
                machine
                and GENERATION_REPORT_CAPABILITY
                in set(machine.control_capabilities or [])
            )
            if agent.placed_on_machine_id != machine_id:
                logger.warning(
                    "lifecycle.replacement_wrong_machine",
                    agent_id=agent_id,
                    expected=agent.placed_on_machine_id,
                    got=machine_id,
                )
                return
            if generation is None:
                if requires_generation or (
                    agent.legacy_report_generation is not None
                    and agent.legacy_report_generation != agent.generation
                ):
                    logger.warning(
                        "lifecycle.replacement_missing_generation",
                        agent_id=agent_id,
                        machine_id=machine_id,
                        expected=agent.generation,
                    )
                    return
            elif generation != agent.generation:
                logger.warning(
                    "lifecycle.replacement_stale_generation",
                    agent_id=agent_id,
                    machine_id=machine_id,
                    expected=agent.generation,
                    got=generation,
                )
                return

            current_generation = int(agent.generation or 0)
            released = await db.execute(
                update(Agent)
                .where(
                    Agent.id == agent_id,
                    Agent.placed_on_machine_id == machine_id,
                    Agent.generation == current_generation,
                )
                .values(
                    placed_on_machine_id=None,
                    pid=None,
                    actual_state="pending",
                    last_crash_reason=reason,
                    lifecycle_lease_token=None,
                    lifecycle_lease_expires_at=None,
                    lifecycle_delivery_state="released",
                )
                .execution_options(synchronize_session=False)
            )
            if released.rowcount != 1:
                await db.rollback()
                return
            db.add(
                ActivityLog(
                    agent_id=agent_id,
                    event_type="replacement_requested",
                    details={
                        "machine_id": machine_id,
                        "generation": current_generation,
                        "reason": reason,
                    },
                )
            )
            await db.commit()

        logger.info(
            "lifecycle.replacement_requested",
            agent_id=agent_id,
            machine_id=machine_id,
            reason=reason,
        )
        # Re-place on a (possibly different) machine.
        await self.request_start(agent_id)

    async def send_sync_batch(self, machine_id: str) -> None:
        """Send a ``sync_batch`` containing all agents placed on *machine_id*."""
        async with self._db_factory() as db:
            result = await db.execute(
                select(Agent).where(Agent.placed_on_machine_id == machine_id)
            )
            agents = result.scalars().all()

            room_rows_by_agent: dict[str, list[str]] = defaultdict(list)
            if agents:
                agent_ids = [agent.id for agent in agents]
                room_rows = await db.execute(
                    select(Participant.agent_id, Participant.room_id).where(
                        Participant.agent_id.in_(agent_ids)
                    )
                )
                for row in room_rows:
                    room_rows_by_agent[row[0]].append(row[1])

            frames: list[dict] = []
            for agent in agents:
                rooms = room_rows_by_agent.get(agent.id, [])
                frame = await self._build_sync_frame(db, agent, rooms)
                frames.append(frame)

            # #510 — commit the per-agent ``anygarden_mcp_token`` rows that
            # ``_build_sync_frame`` → ``_acquire_anygarden_token`` staged via
            # ``db.add``. Without this the minted tokens roll back (never
            # persisted) yet are still shipped in the frame, so every agent
            # (re)spawned via a machine-reconnect batch 401s at ``/mcp/rpc``
            # ("Invalid agent token") and can't call ``mark_task_status``.
            # Committing also fires the ``after_commit`` hook that promotes
            # the tokens into ``_token_cache`` — so a repeated batch reuses
            # the same token (idempotent) instead of re-minting a fresh one.
            # Mirrors ``request_start`` / ``handle_token_request``, which the
            # ``after_commit`` hook comment already lists as the committing
            # batch shapes this path was meant to share.
            await db.commit()

        # ``send_sync_batch`` queries every agent placed on the machine,
        # so the outgoing batch represents the full desired set. Set
        # ``is_full_snapshot=True`` explicitly so the machine treats
        # agents missing from this batch as orphans (#185). Partial
        # updates — if we ever add them — must set the flag to False.
        await self._machine_bus.send(
            machine_id,
            {
                "type": "sync_batch",
                "is_full_snapshot": True,
                "agents": frames,
            },
        )

    async def on_room_added(self, agent_id: str) -> None:
        """Runtime-room-add entry point (#227).

        Called by both ``POST /rooms/{id}/participants`` (agent branch)
        and ``POST /agents/{id}/rooms`` so the two paths share a single
        dispatch policy:

        * Dormant agents (``idle`` / ``stopped`` / ``crashed`` /
          ``pending``) → ``request_start``. Adding a room is the
          moment the "no rooms yet" guard in ``request_start`` finally
          releases, so this is the natural place to retry spawning.
          This matches the behaviour introduced for the 2026-04-12
          "서브에이전트1/2" regression (``test_add_room_redispatches_pending_agent``).
        * Running / starting agents → ``bump_generation``. The agent
          already has a live process and placement, but its
          ``--room`` argv set is now stale. Bumping generation makes
          the next ``sync_desired_state`` authoritative (machine
          re-spawns with refreshed rooms). Before #227 we relied on
          a single best-effort ``JoinRoomOut`` WS push which silently
          dropped when the agent hadn't opened a WS session for the
          specific ``Participant`` row we were targeting — the bug
          this helper closes.
        * Any other state (stopping / stopped with desired_state !=
          running / unknown) → no-op. The admin explicitly arrested
          the agent; a surprise respawn would fight that intent.
        """
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                logger.warning(
                    "lifecycle.on_room_added.agent_not_found", agent_id=agent_id
                )
                return
            state = agent.actual_state

        if state in ("idle", "stopped", "crashed", "pending"):
            await self.request_start(agent_id)
            return
        if state in ("running", "starting"):
            await self.bump_generation(agent_id)
            return

        logger.info(
            "lifecycle.on_room_added.noop",
            agent_id=agent_id,
            actual_state=state,
        )

    async def bump_generation(self, agent_id: str) -> None:
        """Request a config restart, draining active durable leases first."""
        frame: dict | None = None
        target_machine_id: str | None = None
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return
            if agent.desired_state != "running" or not agent.placed_on_machine_id:
                # Preserve the existing config-version contract for idle,
                # stopped, and not-yet-placed agents. There is no live process
                # to drain, but attach/detach and policy changes must still
                # advance the generation consumed by the next spawn.
                old_generation = int(agent.generation or 0)
                agent.generation = old_generation + 1
                await db.execute(
                    update(AgentTurnAttempt)
                    .where(
                        AgentTurnAttempt.agent_id == agent.id,
                        AgentTurnAttempt.state == "pending",
                        AgentTurnAttempt.generation == old_generation,
                    )
                    .values(generation=agent.generation)
                )
                await db.commit()
                return
            room_result = await db.execute(
                select(Participant.room_id).where(Participant.agent_id == agent.id)
            )
            rooms = [row[0] for row in room_result.all()]
            candidate = await self._build_sync_frame(db, agent, rooms)
            candidate_hash = _manifest_hash(candidate, agent=agent)

            # Only the effective, materialized manifest may restart a live
            # process. API mutations can be semantically filtered (for
            # example, an attached but unapproved skill), so the raw write is
            # not itself proof that a generation change is needed.
            if candidate_hash == agent.manifest_hash:
                if agent.pending_generation is not None:
                    cancelled_generation = agent.pending_generation
                    agent.pending_generation = None
                    agent.restart_requested_at = None
                    agent.restart_deadline_at = None
                    agent.pending_manifest_hash = None
                    await db.execute(
                        update(AgentTurnAttempt)
                        .where(
                            AgentTurnAttempt.agent_id == agent.id,
                            AgentTurnAttempt.state == "pending",
                            AgentTurnAttempt.generation == cancelled_generation,
                        )
                        .values(generation=agent.generation)
                    )
                    db.add(
                        ActivityLog(
                            agent_id=agent.id,
                            event_type="generation_drain_cancelled",
                            details={
                                "generation": agent.generation,
                                "cancelled_generation": cancelled_generation,
                                "reason": "effective_manifest_unchanged",
                            },
                        )
                    )
                await db.commit()
                return
            if candidate_hash == agent.pending_manifest_hash:
                # Coalesce repeated API hooks for the same desired manifest;
                # one pending drain/restart is sufficient.
                await db.commit()
                return

            from anygarden.turns.service import active_lease_count

            active = await active_lease_count(
                db, agent_id=agent.id, generation=int(agent.generation or 0)
            )
            if active:
                now = datetime.now(timezone.utc)
                try:
                    drain_sec = max(
                        1, int(os.environ.get("ANYGARDEN_GENERATION_DRAIN_SEC", "60"))
                    )
                except ValueError:
                    drain_sec = 60
                agent.pending_generation = int(
                    (agent.pending_generation or agent.generation or 0) + 1
                )
                agent.restart_requested_at = now
                agent.restart_deadline_at = now + timedelta(seconds=drain_sec)
                agent.pending_manifest_hash = candidate_hash
                db.add(
                    ActivityLog(
                        agent_id=agent.id,
                        event_type="generation_drain_requested",
                        details={
                            "generation": agent.generation,
                            "pending_generation": agent.pending_generation,
                            "active_leases": active,
                            "deadline": agent.restart_deadline_at.isoformat(),
                        },
                    )
                )
            else:
                old_generation = int(agent.generation or 0)
                agent.generation = old_generation + 1
                candidate["generation"] = agent.generation
                agent.manifest_hash = candidate_hash
                await db.execute(
                    update(AgentTurnAttempt)
                    .where(
                        AgentTurnAttempt.agent_id == agent.id,
                        AgentTurnAttempt.state == "pending",
                        AgentTurnAttempt.generation == old_generation,
                    )
                    .values(generation=agent.generation)
                )
                frame = candidate
                target_machine_id = agent.placed_on_machine_id

            await db.commit()

        # Send outside the session context if needed.
        if frame is not None and target_machine_id is not None:
            await self._machine_bus.send(target_machine_id, frame)

    async def release_generation_drain(self, agent_id: str) -> bool:
        """Promote a pending generation once its old leases reach zero."""

        frame: dict | None = None
        target_machine_id: str | None = None
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None or agent.pending_generation is None:
                return False
            from anygarden.turns.service import active_lease_count

            active = await active_lease_count(
                db, agent_id=agent.id, generation=int(agent.generation or 0)
            )
            if active:
                return False
            room_result = await db.execute(
                select(Participant.room_id).where(Participant.agent_id == agent.id)
            )
            rooms = [row[0] for row in room_result.all()]
            agent.generation = agent.pending_generation
            agent.pending_generation = None
            agent.manifest_hash = agent.pending_manifest_hash
            agent.pending_manifest_hash = None
            agent.restart_requested_at = None
            agent.restart_deadline_at = None
            if agent.desired_state == "running" and agent.placed_on_machine_id:
                frame = await self._build_sync_frame(db, agent, rooms)
                frame["generation"] = agent.generation
                target_machine_id = agent.placed_on_machine_id
            db.add(
                ActivityLog(
                    agent_id=agent.id,
                    event_type="generation_drain_completed",
                    details={"generation": agent.generation},
                )
            )
            await db.commit()
        if frame is not None and target_machine_id is not None:
            await self._machine_bus.send(target_machine_id, frame)
        return True

    # ── Internal helpers ──────────────────────────────────────

    async def _get_agent(self, db: AsyncSession, agent_id: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def _resolve_skill_files(
        self, db: AsyncSession, agent_id: str
    ) -> dict[str, str]:
        """Merge attached skills into ``{path: body}``, filtering unapproved.

        Mirrors ``SkillLibraryService.resolve_for_agent`` so lifecycle
        can stay service-injection-free for tests that only care about
        agent files. Both code paths apply the same
        ``approved_by IS NOT NULL`` gate — the service version is what
        the REST layer calls for cache-friendly previews, while this
        one runs in the hot spawn path.

        Unapproved attachments trip a structlog warning: the UI and
        API both refuse to attach unapproved skills, so the only way
        this observation can fire in production is a race (approve →
        attach → reject without detach) or a manual DB edit — either
        of which an operator wants to see.
        """
        rows = (
            (
                await db.execute(
                    select(SkillLibraryEntry)
                    .join(
                        AgentSkill,
                        AgentSkill.skill_library_id == SkillLibraryEntry.id,
                    )
                    .where(AgentSkill.agent_id == agent_id)
                )
            )
            .scalars()
            .all()
        )
        files: dict[str, str] = {}
        for entry in rows:
            if entry.approved_by is None:
                logger.warning(
                    "lifecycle.skill_attached_but_unapproved",
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

    async def _build_sync_frame(
        self,
        db: AsyncSession,
        agent: Agent,
        rooms: list[str],
    ) -> dict:
        """Build a ``sync_desired_state`` dict from DB data."""
        # Agent files
        file_rows = (
            (await db.execute(select(AgentFile).where(AgentFile.agent_id == agent.id)))
            .scalars()
            .all()
        )
        files_map: dict[str, str] = {row.path: row.content for row in file_rows}

        # #119 / #123 / #125 — merge attached *approved* library skills
        # into the same files map. Delegating to
        # ``SkillLibraryService.resolve_for_agent`` keeps the approval
        # gate in a single place (service layer) — unapproved skills
        # are filtered there, with a structlog warning when an
        # unapproved attachment is observed (see service docstring).
        # AgentFile entries win on key collision because they represent
        # an explicit admin override uploaded directly to the agent —
        # ``setdefault`` is load-bearing here.
        skill_files = await self._resolve_skill_files(db, agent.id)
        for path, body in skill_files.items():
            files_map.setdefault(path, body)

        # #124 — overlay attached MCP server instances onto the
        # engine-specific settings file. When the admin already
        # uploaded a settings file as an AgentFile, we merge the
        # template overlays into that base so admin-authored keys
        # (permissions / env / custom mcpServers overrides) are
        # preserved — but when there's no admin file the overlay
        # seeds a fresh one. ``MCPTemplateService.render_for_agent``
        # returns ``{}`` for agents with no attachments or engines
        # without MCP support, making this block cheap.
        # #124 + #277 — overlay attached MCP server instances onto
        # the engine-specific settings file, with the anygarden self-MCP
        # entry prepended to every supported engine. ``setdefault``-
        # based merge in ``merge_for_engine`` means an admin who
        # explicitly attaches an external server named ``anygarden``
        # still wins (escape hatch — see plan §3.2 for #277).
        from anygarden.mcp_templates.merge import (
            anygarden_default_entry,
            merge_for_engine,
            render_instance,
            settings_path_for_engine,
        )

        mcp_engine = agent.engine
        settings_path = settings_path_for_engine(mcp_engine)
        # ``anygarden_token`` lives at this scope so the spawn-frame
        # builder below can echo it on the ``anygarden_mcp_token`` field
        # regardless of whether settings_path was None.
        anygarden_token: str | None = None
        if settings_path is not None:
            overlays = []

            # Admin-attached external MCP templates first (existing
            # #124). Order matters because ``merge_for_engine`` uses
            # ``setdefault`` semantics — *earlier* entries win on key
            # collision. Putting admin attachments first preserves
            # the escape hatch from plan §3.2 결정 1: an admin who
            # explicitly registers an external server named ``anygarden``
            # overrides the builtin instead of getting silently
            # shadowed by it.
            if self._mcp_template_service is not None:
                pairs = await self._mcp_template_service.list_instances_for_agent(
                    db,
                    agent.id,
                )
                secrets = self._mcp_template_service._secrets
                for instance, template in pairs:
                    if not instance.enabled:
                        continue
                    env_values = secrets.decrypt_dict(
                        instance.env_values_encrypted,
                    )
                    rendered = render_instance(
                        name=template.name,
                        config_per_engine=template.config_per_engine or {},
                        env_values=env_values,
                        engine=mcp_engine,
                    )
                    if rendered is not None:
                        overlays.append(rendered)

            # Built-in anygarden self-MCP — issued only when we know how
            # to reach the cluster externally. Tests that omit
            # ``cluster_external_url`` skip this entirely so they
            # don't have to mint per-spawn agent tokens.
            if self._cluster_external_url:
                default = anygarden_default_entry(
                    engine=mcp_engine,
                    cluster_url=self._cluster_external_url,
                    agent_token="<placeholder>",
                )
                if default is not None:
                    # Issue #369 — token now comes from the per-agent
                    # cache (mint on first hit per spawn cycle, reuse
                    # thereafter). Pre-#369 this minted a fresh token
                    # *per frame build*, which made every read-only
                    # rebuild path (broadcast snapshot, sync_batch
                    # tick) stage an uncommitted ``agent_tokens`` row
                    # while still updating the manifest_store cache —
                    # leaving agent processes with stdin-piped tokens
                    # that the DB never persisted.
                    anygarden_token = self._acquire_anygarden_token(db, agent.id)
                    real_default = anygarden_default_entry(
                        engine=mcp_engine,
                        cluster_url=self._cluster_external_url,
                        agent_token=anygarden_token,
                    )
                    assert real_default is not None  # narrowed above
                    overlays.append(real_default)

            if overlays:
                admin_content = files_map.get(settings_path)
                files_map[settings_path] = merge_for_engine(
                    engine=mcp_engine,
                    admin_content=admin_content,
                    overlays=overlays,
                )

        # Issue #359 — for openhands agents, ensure we mint a token
        # even when the agent has no MCP overlays attached. The MCP
        # block above only mints when ``default is not None`` (engine
        # has a anygarden_default_entry mapping AND the agent has files
        # to write to). openhands consumes ``.mcp.json`` so usually
        # gets a token there, but the gateway path needs to work even
        # if MCP rendering happens to skip (e.g. cluster_external_url
        # set but the engine's settings_path is None for some future
        # variant). The reverse proxy's ``get_current_identity``
        # validates this same ``agent_tokens`` row, so reusing the
        # MCP-minted token is safe — both endpoints accept it.
        if (
            anygarden_token is None
            and self._llm_gateway_enabled
            and self._cluster_external_url
            and agent.engine == "openhands"
        ):
            # Issue #369 — same cached path as the MCP block above so
            # gateway-only agents (no MCP attachments) still get a
            # stable, committed token.
            anygarden_token = self._acquire_anygarden_token(db, agent.id)

        # Sub-rooms
        sub_rooms_info: list[dict[str, str | None]] = []
        if rooms:
            sub_result = await db.execute(
                select(Room.name, Room.description)
                .where(Room.parent_room_id.in_(rooms))
                .order_by(Room.name)
            )
            for name, desc in sub_result.all():
                sub_rooms_info.append({"name": name, "description": desc})

        workspace_attachment = (
            await db.execute(
                select(WorkspaceAttachment).where(
                    WorkspaceAttachment.agent_id == agent.id,
                    WorkspaceAttachment.state == "active",
                )
            )
        ).scalar_one_or_none()
        workspace_descriptor = None
        if workspace_attachment is not None:
            from anygarden.workspaces.service import attachment_frame

            workspace_descriptor = attachment_frame(workspace_attachment)

        return {
            "type": "sync_desired_state",
            "agent_id": agent.id,
            "desired_state": agent.desired_state,
            "generation": agent.generation,
            "engine": agent.engine,
            "name": agent.name,
            "profile_yaml": agent.profile_yaml or "",
            "rooms": rooms,
            "agents_md": agent.agents_md,
            # Issue #237 — DB snapshot of the long-term memory file. The
            # machine seeds ``memory/notes.md`` from this on materialize;
            # subsequent file writes by the agent flow back via
            # ``agent_memory_update`` frames.
            "memory_md": agent.memory_md,
            "files": files_map,
            # Issue #359 — gateway env vars for openhands only. The
            # helper guards on engine name + flag + URL + token, so
            # passing all the conditions through cleanly returns
            # ``{}`` for any case that doesn't satisfy them. This
            # preserves pre-#359 behaviour (``engine_secrets={}``) for
            # the three CLI engines and for openhands agents on
            # deployments that haven't enabled the gateway yet.
            "engine_secrets": build_engine_secrets(
                engine=agent.engine,
                gateway_enabled=self._llm_gateway_enabled,
                cluster_external_url=self._cluster_external_url,
                agent_token=anygarden_token,
            ),
            "reasoning_effort": agent.reasoning_effort,
            "model": agent.model,
            # #309 — semantic permission tier; the machine forwards it
            # into the agent process env (``ANYGARDEN_AGENT_PERMISSION_LEVEL``)
            # and each engine adapter translates to native dials.
            "permission_level": agent.permission_level,
            # #493 — per-agent turn timeout; the machine forwards it into the
            # agent process env (``ANYGARDEN_AGENT_TURN_TIMEOUT_SEC``) where the
            # engine adapters resolve it (see ``_turn_timeout``). None = global
            # default.
            "turn_timeout_sec": agent.turn_timeout_sec,
            "sub_rooms": sub_rooms_info,
            "restart_policy": agent.restart_policy,
            "max_restarts": agent.max_restarts,
            "restart_window_seconds": agent.restart_window_seconds,
            # Issue #73 — forward the runtime selector to the machine
            # daemon so it spawns via the right binary path. Pre-#73
            # machines ignore the unknown key and fall back to the
            # SpawnManifest default of ``"python"``.
            "runtime": getattr(agent, "runtime", "python") or "python",
            # #277 — Plaintext bearer token for the anygarden self-MCP
            # entry the cluster just baked into ``files[<settings>]``.
            # Codex agents need this exposed in their process env as
            # ``ANYGARDEN_AGENT_TOKEN`` because their .codex/config.toml
            # references it via ``bearer_token_env_var`` rather than
            # storing the secret on disk. claude-code / gemini-cli
            # already see the literal ``Authorization`` header in the
            # rendered settings file, so they don't strictly need
            # this field — but the machine may still inject it for
            # consistency. ``None`` means we did not register the
            # builtin (e.g. cluster_external_url unset, or engine
            # has no MCP support).
            "anygarden_mcp_token": anygarden_token,
            # Opaque lease metadata only. Canonical host paths remain in the
            # machine-local registry and cannot enter a desired-state frame.
            "workspace_attachment": workspace_descriptor,
        }


# ── Issue #204 — orphan sweeper ──────────────────────────────────────


#: Default age at which a ``handler_started`` without a matching
#: ``handler_finished`` is promoted to ``handler_orphaned``. Sized
#: as the agent-side engine timeout (15 min) plus 5 min of slack
#: for reconnects/cluster hops. Overridable per call for tests, and
#: at the call site via the ``ANYGARDEN_REQUEST_LIVENESS_SEC`` env var
#: (#481 — the server-side liveness watchdog).
ORPHAN_THRESHOLD_SEC_DEFAULT = 1200


@dataclass(frozen=True)
class OrphanedRequest:
    """A request the sweep just promoted to ``handler_orphaned`` (#481).

    Carries the room/agent context the liveness watchdog needs to make
    the orphan *visible* (room system notice) and *recoverable* (Task
    re-dispatch) — see ``notify_and_redispatch_orphans``. ``room_id`` may
    be ``None`` for legacy ``handler_started`` rows that never recorded
    it inside ``details``.
    """

    request_id: str
    agent_id: str | None
    room_id: str | None


async def sweep_orphaned_requests(
    session_factory,
    *,
    threshold_sec: int = ORPHAN_THRESHOLD_SEC_DEFAULT,
) -> list[OrphanedRequest]:
    """Mark stalled handlers as ``handler_orphaned``.

    A ``request_id`` is promoted to ``handler_orphaned`` (a single row
    inserted) when it has a ``handler_started`` with no terminal event
    (``handler_finished`` or a prior ``handler_orphaned``) yet, and
    *either* of:

    - **slow path (#204)** — its earliest event is older than
      *threshold_sec*. The "alive but hung forever" backstop.
    - **fast path (#481)** — the request's agent has been flipped to
      ``actual_state == "crashed"`` (by ``sweep_stale_agents`` / a death
      report). The threshold is ignored: the process is known dead, so
      its in-flight turn can never deliver and is orphaned immediately
      (~minutes instead of ~20).

    Idempotent by construction: the ``HAVING`` clause excludes
    already-orphaned requests. Returns the newly-orphaned requests as
    ``OrphanedRequest`` rows (#427/#481) so the caller can bump the
    orphan metric, reap the matching in-memory spans, and surface +
    re-dispatch them; ``len()`` is the count.
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=threshold_sec)

    started_expr = func.sum(
        case((ActivityLog.event_type == "handler_started", 1), else_=0)
    ).label("n_started")
    terminal_expr = func.sum(
        case(
            (
                ActivityLog.event_type.in_(["handler_finished", "handler_orphaned"]),
                1,
            ),
            else_=0,
        )
    ).label("n_terminal")
    earliest_ts = func.min(ActivityLog.timestamp).label("started_at")

    async with session_factory() as db:
        # #481 — left-join the owning agent so the fast path can admit a
        # request whose agent is ``crashed`` even when its rows are still
        # recent (younger than *threshold*). The outer join keeps requests
        # whose ``agent_id`` is NULL / unknown on the slow path only.
        stmt = (
            select(
                ActivityLog.request_id,
                ActivityLog.agent_id,
                earliest_ts,
            )
            .select_from(ActivityLog)
            .outerjoin(Agent, Agent.id == ActivityLog.agent_id)
            .where(
                ActivityLog.request_id.isnot(None),
                ~select(AgentTurn.request_id)
                .where(AgentTurn.request_id == ActivityLog.request_id)
                .exists(),
                or_(
                    ActivityLog.timestamp < threshold,
                    Agent.actual_state == "crashed",
                ),
            )
            .group_by(ActivityLog.request_id, ActivityLog.agent_id)
            .having(and_(started_expr > 0, terminal_expr == 0))
        )

        rows = (await db.execute(stmt)).all()

        # ``room_id`` lives inside the JSON ``details`` and varies
        # across dialects' JSON path syntax; fetch it with a second
        # pass by looking up one handler_started row per group.
        # This is fine at orphan scale — orphans are rare by design
        # (engine_timeout already closes the common case).
        orphaned: list[OrphanedRequest] = []
        for req_id, agent_id, started_at in rows:
            started_row = (
                await db.execute(
                    select(ActivityLog.details)
                    .where(
                        ActivityLog.request_id == req_id,
                        ActivityLog.event_type == "handler_started",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            room_id = None
            if isinstance(started_row, dict):
                room_id = started_row.get("room_id")

            db.add(
                ActivityLog(
                    agent_id=agent_id,
                    event_type="handler_orphaned",
                    request_id=req_id,
                    room_id=room_id,
                    details={
                        "room_id": room_id,
                        "started_at": started_at.isoformat()
                        if started_at is not None
                        else None,
                        "threshold_sec": threshold_sec,
                    },
                )
            )
            orphaned.append(
                OrphanedRequest(
                    request_id=req_id,
                    agent_id=agent_id,
                    room_id=room_id,
                )
            )
        await db.commit()
        return orphaned


# ── #481 — orphan visibility + recovery ──────────────────────────────


#: One-line Korean room notice posted when a request is orphaned by the
#: liveness watchdog (#481). Keep it short and reassuring — the matching
#: Task (if assignment-originated) is re-dispatched right after.
_ORPHAN_NOTICE_TEXT = "⚠️ 에이전트 응답이 확인되지 않아 이 요청을 종료 처리했습니다."


async def notify_and_redispatch_orphans(
    session_factory,
    manager,
    rows: list[OrphanedRequest],
) -> None:
    """Make freshly-orphaned requests visible and recoverable (#481).

    For each ``OrphanedRequest`` the sweep just produced:

    1. **Visibility** — append a system notice (``participant_id=None``,
       ``metadata.system_origin == "liveness_orphan"``) to the request's
       room and broadcast it as a ``MessageOut`` so live subscribers see
       that the silent turn was closed out. Skipped when *room_id* is
       unknown, or when *manager* is ``None`` (broadcast still relies on a
       manager; the row is persisted regardless so a reconnect replays it).
    2. **Recovery** — if the request maps to an assignment-originated Task,
       re-dispatch it once via ``_redispatch_task_by_request_id`` (the same
       bounded core the WS ``handler_finished`` path uses). Live turns (no
       mapping) get only the notice — the scope invariant is preserved.

    Fully fail-soft: each row is handled in its own ``try`` so one bad row
    (or a broadcast / re-dispatch error) never blocks the remaining rows,
    and never touches the orphan-marking commit the sweep already made
    (separate sessions throughout).
    """
    if not rows:
        return

    # Lazy imports keep the scheduler↔ws / scheduler↔messages module load
    # order cycle-free (mirrors the ws handler's lazy ``messages.service``
    # import on its re-dispatch path).
    from anygarden.db.repository import append_message
    from anygarden.messages.serialization import message_to_frame
    from anygarden.ws.handler import _redispatch_task_by_request_id

    for row in rows:
        # --- 1. visibility: room system notice ---
        if row.room_id and manager is not None:
            try:
                async with session_factory() as db:
                    msg = await append_message(
                        db,
                        row.room_id,
                        None,
                        _ORPHAN_NOTICE_TEXT,
                        {
                            "system_origin": "liveness_orphan",
                            "request_id": row.request_id,
                        },
                    )
                    await db.commit()
                    frame = message_to_frame(msg)
                await manager.broadcast(row.room_id, frame)
            except Exception as exc:  # noqa: BLE001 — notice is best-effort
                logger.warning(
                    "liveness.orphan_notice.failed",
                    request_id=row.request_id,
                    room_id=row.room_id,
                    error=str(exc),
                )

        # --- 2. recovery: re-dispatch the assignment Task (if any) ---
        try:
            async with session_factory() as db:
                did = await _redispatch_task_by_request_id(
                    db,
                    request_id=row.request_id,
                    reason="liveness_orphan",
                    manager=manager,
                )
                if did:
                    await db.commit()
        except Exception as exc:  # noqa: BLE001 — recovery is best-effort
            logger.warning(
                "liveness.orphan_redispatch.failed",
                request_id=row.request_id,
                error=str(exc),
            )


# ── #447 Wave 1a — agent heartbeat reaper ────────────────────────────


#: Default age at which an agent reporting ``actual_state == "running"``
#: but whose ``last_heartbeat_at`` has gone silent is promoted to
#: ``crashed`` — *provided* its placed machine is also not online. Two
#: minutes matches the daemon's default report cadence plus slack;
#: overridable per call for tests and via the
#: ``ANYGARDEN_HEARTBEAT_STALE_SEC`` env var at the call site.
STALE_HEARTBEAT_SEC_DEFAULT = 120


async def sweep_stale_agents(
    session_factory,
    *,
    threshold_sec: int = STALE_HEARTBEAT_SEC_DEFAULT,
) -> int:
    """Flip agents stuck ``running`` on a dead machine to ``crashed``.

    A machine that loses power never sends a final ``report_actual_state``,
    so its agents linger at ``actual_state == "running"`` forever and keep
    polluting bin-pack placement (placement counts ``running``). This
    sweep reaps them on a **dual gate**: the agent's ``last_heartbeat_at``
    is older than *threshold_sec* AND the machine it is placed on is not
    ``online``. Both must hold — a stale heartbeat alone could just be a
    slow report from a live machine.

    CRITICAL GUARD: only ``actual_state == "running"`` rows with a
    non-NULL ``last_heartbeat_at`` are eligible. ``last_heartbeat_at`` is
    only stamped on the running transition (see
    ``handle_report_actual_state``), so a ``starting`` agent mid-spawn
    legitimately has a NULL/old heartbeat and must never be reaped here.

    For each matched agent: set ``actual_state = "crashed"``, record a
    short ``last_crash_reason``, and add a ``state_changed`` ActivityLog
    row recording ``from``/``to``/``reason``. Returns the count of agents
    crashed so the caller can bump the sweep metric. Idempotent: once an
    agent is ``crashed`` it no longer matches the ``running`` filter.
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=threshold_sec)

    async with session_factory() as db:
        stmt = (
            select(Agent)
            .join(Machine, Agent.placed_on_machine_id == Machine.id)
            .where(
                Agent.actual_state == "running",
                Agent.last_heartbeat_at.isnot(None),
                Agent.last_heartbeat_at < threshold,
                Agent.placed_on_machine_id.isnot(None),
                Machine.status != "online",
            )
        )
        agents = (await db.execute(stmt)).scalars().all()

        for agent in agents:
            old_state = agent.actual_state
            agent.actual_state = "crashed"
            agent.last_crash_reason = "heartbeat_stale"
            db.add(
                ActivityLog(
                    agent_id=agent.id,
                    event_type="state_changed",
                    details={
                        "from": old_state,
                        "to": "crashed",
                        "reason": "heartbeat_stale",
                        "threshold_sec": threshold_sec,
                    },
                )
            )

        await db.commit()
        return len(agents)
