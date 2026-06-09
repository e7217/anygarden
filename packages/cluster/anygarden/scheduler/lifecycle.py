"""Declarative desired-state agent lifecycle.

The server tells each machine what agents it *should* run
(``sync_desired_state`` / ``sync_batch``).  The machine autonomously
converges toward that desired state by spawning, killing, or restarting
processes.  It reports back with ``report_actual_state`` and may request
tokens or replacement placement as needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.db.models import (
    ActivityLog, Agent, AgentFile, AgentSkill, AgentToken, Participant, Room,
    SkillLibraryEntry,
)
from anygarden.scheduler.gateway_secrets import build_engine_secrets
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.scheduler.placement import NoSuitableMachineError, select_machine_for

logger = structlog.get_logger(__name__)


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

    # ── Public API ──────────────────────────────────────────────

    async def request_start(self, agent_id: str) -> None:
        """Select a machine, bump generation, send ``sync_desired_state``."""
        # Issue #369 — invalidate any cached anygarden_token on each
        # explicit start. The new spawn frame mints a fresh token and
        # re-populates the cache; subsequent rebuilds reuse it. Without
        # this evict, a stop → start cycle would reuse the previous
        # spawn's plaintext, which is undesirable both for security
        # rotation and for race scenarios where the prior process
        # outlives the stop window.
        self._token_cache.pop(agent_id, None)
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                logger.error("lifecycle.agent_not_found", agent_id=agent_id)
                return

            # Refuse to dispatch if agent has no room memberships.
            result = await db.execute(
                select(Participant.room_id).where(Participant.agent_id == agent.id)
            )
            rooms = [row[0] for row in result.all()]
            if not rooms:
                logger.warning("lifecycle.spawn_refused_no_rooms", agent_id=agent_id)
                agent.actual_state = "pending"
                agent.desired_state = "running"
                agent.placed_on_machine_id = None
                agent.pid = None
                agent.last_crash_reason = (
                    "no rooms assigned \u2014 add the agent to at least one room "
                    "before starting"
                )
                await db.commit()
                return

            try:
                machine = await select_machine_for(
                    agent.engine, db, self._machine_bus
                )
            except NoSuitableMachineError:
                logger.warning(
                    "lifecycle.no_machine",
                    agent_id=agent_id,
                    engine=agent.engine,
                )
                agent.actual_state = "pending"
                await db.commit()
                return

            agent.placed_on_machine_id = machine.id
            agent.desired_state = "running"
            agent.actual_state = "pending"
            agent.generation = (agent.generation or 0) + 1
            agent.started_at = datetime.now(timezone.utc)

            frame = await self._build_sync_frame(db, agent, rooms)
            db.add(ActivityLog(
                agent_id=agent_id,
                event_type="start_requested",
                details={"machine_id": machine.id, "generation": agent.generation},
            ))
            await db.commit()

            sent = await self._machine_bus.send(machine.id, frame)
            if not sent:
                logger.warning(
                    "lifecycle.sync_send_failed",
                    agent_id=agent_id,
                    machine_id=machine.id,
                )

    def _acquire_anygarden_token(
        self, db: AsyncSession, agent_id: str
    ) -> str:
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
        """
        cached = self._token_cache.get(agent_id)
        if cached is not None:
            return cached
        token = generate_token()
        token_hash, lookup_hint = hash_agent_token(token)
        db.add(
            AgentToken(
                agent_id=agent_id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            )
        )
        self._token_cache[agent_id] = token
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
        """Set desired_state='stopped' and push ``sync_desired_state``.

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
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return

            agent.desired_state = "stopped"
            if agent.placed_on_machine_id:
                if agent.actual_state in ("running", "starting", "pending"):
                    agent.actual_state = "stopping"
            else:
                # Orphan path — no daemon will ever confirm the stop,
                # so absent-from-report convergence won't fire either.
                # Short-circuit to stopped to avoid a stuck row.
                agent.actual_state = "stopped"
                agent.pid = None
            db.add(ActivityLog(agent_id=agent_id, event_type="stop_requested"))
            await db.commit()

            if agent.placed_on_machine_id:
                await self._machine_bus.send(agent.placed_on_machine_id, {
                    "type": "sync_desired_state",
                    "agent_id": agent.id,
                    "desired_state": "stopped",
                    "generation": agent.generation,
                })

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
        async with self._db_factory() as db:
            for entry in agents_data:
                aid = entry.get("agent_id")
                if not aid:
                    continue
                agent = await self._get_agent(db, aid)
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
                old_state = agent.actual_state
                if new_state:
                    agent.actual_state = new_state
                if "pid" in entry:
                    agent.pid = entry["pid"]
                if "last_crash_reason" in entry:
                    agent.last_crash_reason = entry["last_crash_reason"]
                if new_state == "running":
                    agent.last_heartbeat_at = datetime.now(timezone.utc)

                # Only log when state actually changed (skip heartbeat noise)
                if new_state and new_state != old_state:
                    db.add(ActivityLog(
                        agent_id=aid,
                        event_type="state_changed",
                        details={
                            "from": old_state,
                            "to": new_state,
                            "pid": entry.get("pid"),
                            "machine_id": machine_id,
                        },
                    ))
                    if new_state == "running":
                        backfill_targets.append(aid)
            # Agents placed on this machine but absent from the report:
            # if desired=stopped they are confirmed stopped. Keep
            # placed_on_machine_id so the machine page still lists them.
            reported_ids = {e.get("agent_id") for e in agents_data if e.get("agent_id")}
            placed_on_machine = (await db.execute(
                select(Agent).where(
                    Agent.placed_on_machine_id == machine_id,
                )
            )).scalars().all()
            for agent in placed_on_machine:
                if agent.id in reported_ids:
                    continue
                if agent.desired_state == "stopped" and agent.actual_state != "stopped":
                    old = agent.actual_state
                    agent.actual_state = "stopped"
                    agent.pid = None
                    db.add(ActivityLog(
                        agent_id=agent.id,
                        event_type="state_changed",
                        details={"from": old, "to": "stopped", "machine_id": machine_id, "reason": "absent_from_report"},
                    ))

            await db.commit()

        # #255 — Flush shared-file backfill for newly-running agents.
        # Runs outside the state-update transaction so a backfill
        # failure can't roll back the actual_state commit. The daemon
        # compares ``content_sha256`` per file and skips rewrites, so
        # occasional double-sends (e.g. pending→starting→running
        # transitions arriving in separate frames) are harmless.
        if self._room_files_dir is not None and backfill_targets:
            await self._backfill_shared_files_for_agents(backfill_targets)

    async def _backfill_shared_files_for_agents(
        self, agent_ids: list[str]
    ) -> None:
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
            for aid in agent_ids:
                rooms = [
                    row[0]
                    for row in (
                        await db.execute(
                            select(Participant.room_id).where(
                                Participant.agent_id == aid
                            )
                        )
                    ).all()
                ]
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
                db.add(AgentToken(
                    agent_id=agent.id,
                    token_hash=token_hash,
                    lookup_hint=lookup_hint,
                ))
                grants.append({
                    "type": "token_grant",
                    "agent_id": agent.id,
                    "agent_token": plain,
                })
            await db.commit()
        return grants

    async def handle_request_replacement(
        self,
        machine_id: str,
        agent_id: str,
        reason: str,
    ) -> None:
        """Machine requests the server to re-place an agent elsewhere."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return
            agent.placed_on_machine_id = None
            agent.pid = None
            agent.actual_state = "pending"
            agent.last_crash_reason = reason
            db.add(ActivityLog(
                agent_id=agent_id,
                event_type="replacement_requested",
                details={"machine_id": machine_id, "reason": reason},
            ))
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

            frames: list[dict] = []
            for agent in agents:
                room_result = await db.execute(
                    select(Participant.room_id).where(
                        Participant.agent_id == agent.id,
                    )
                )
                rooms = [row[0] for row in room_result.all()]
                frame = await self._build_sync_frame(db, agent, rooms)
                frames.append(frame)

        # ``send_sync_batch`` queries every agent placed on the machine,
        # so the outgoing batch represents the full desired set. Set
        # ``is_full_snapshot=True`` explicitly so the machine treats
        # agents missing from this batch as orphans (#185). Partial
        # updates — if we ever add them — must set the flag to False.
        await self._machine_bus.send(machine_id, {
            "type": "sync_batch",
            "is_full_snapshot": True,
            "agents": frames,
        })

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
                logger.warning("lifecycle.on_room_added.agent_not_found", agent_id=agent_id)
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
        """Increment generation and push ``sync_desired_state`` if running."""
        async with self._db_factory() as db:
            agent = await self._get_agent(db, agent_id)
            if agent is None:
                return
            agent.generation = (agent.generation or 0) + 1
            await db.commit()

            if (
                agent.desired_state == "running"
                and agent.placed_on_machine_id
            ):
                room_result = await db.execute(
                    select(Participant.room_id).where(
                        Participant.agent_id == agent.id,
                    )
                )
                rooms = [row[0] for row in room_result.all()]
                frame = await self._build_sync_frame(db, agent, rooms)

        # Send outside the session context if needed.
        if (
            agent.desired_state == "running"
            and agent.placed_on_machine_id
        ):
            await self._machine_bus.send(agent.placed_on_machine_id, frame)

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
            await db.execute(
                select(AgentFile).where(AgentFile.agent_id == agent.id)
            )
        ).scalars().all()
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
                    db, agent.id,
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
                select(Room.name, Room.description).where(
                    Room.parent_room_id.in_(rooms)
                ).order_by(Room.name)
            )
            for name, desc in sub_result.all():
                sub_rooms_info.append({"name": name, "description": desc})

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
        }


# ── Issue #204 — orphan sweeper ──────────────────────────────────────


#: Default age at which a ``handler_started`` without a matching
#: ``handler_finished`` is promoted to ``handler_orphaned``. Sized
#: as the agent-side engine timeout (15 min) plus 5 min of slack
#: for reconnects/cluster hops. Overridable per call for tests.
ORPHAN_THRESHOLD_SEC_DEFAULT = 1200


async def sweep_orphaned_requests(
    session_factory,
    *,
    threshold_sec: int = ORPHAN_THRESHOLD_SEC_DEFAULT,
) -> list[str]:
    """Mark stalled handlers as ``handler_orphaned``.

    For every ``request_id`` whose earliest ``handler_started`` is
    older than *threshold_sec* and which has no terminal event
    (``handler_finished`` or prior ``handler_orphaned``) yet,
    insert a single ``handler_orphaned`` row.

    Idempotent by construction: the ``HAVING`` clause excludes
    already-orphaned requests. Returns the list of newly-orphaned
    ``request_id``s (#427) so the caller can bump the orphan metric and
    reap the matching in-memory spans; ``len()`` is the count.
    """
    threshold = datetime.now(timezone.utc) - timedelta(seconds=threshold_sec)

    started_expr = func.sum(
        case((ActivityLog.event_type == "handler_started", 1), else_=0)
    ).label("n_started")
    terminal_expr = func.sum(
        case(
            (
                ActivityLog.event_type.in_(
                    ["handler_finished", "handler_orphaned"]
                ),
                1,
            ),
            else_=0,
        )
    ).label("n_terminal")
    earliest_ts = func.min(ActivityLog.timestamp).label("started_at")

    async with session_factory() as db:
        stmt = (
            select(
                ActivityLog.request_id,
                ActivityLog.agent_id,
                earliest_ts,
            )
            .where(
                ActivityLog.request_id.isnot(None),
                ActivityLog.timestamp < threshold,
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
        orphaned_ids: list[str] = []
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
            orphaned_ids.append(req_id)
        await db.commit()
        return orphaned_ids
