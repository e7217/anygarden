"""Declarative desired-state agent lifecycle.

The server tells each machine what agents it *should* run
(``sync_desired_state`` / ``sync_batch``).  The machine autonomously
converges toward that desired state by spawning, killing, or restarting
processes.  It reports back with ``report_actual_state`` and may request
tokens or replacement placement as needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from doorae.auth.token import generate_token, hash_agent_token
from doorae.db.models import (
    ActivityLog, Agent, AgentFile, AgentSkill, AgentToken, Participant, Room,
    SkillLibraryEntry,
)
from doorae.scheduler.machine_bus import MachineBus
from doorae.scheduler.placement import NoSuitableMachineError, select_machine_for

logger = structlog.get_logger(__name__)


class AgentLifecycle:
    """Declarative lifecycle: the server pushes *desired* state to machines
    and reacts to state reports from them."""

    def __init__(
        self,
        db_factory,
        machine_bus: MachineBus,
        server_url: str = "",
        *,
        mcp_template_service=None,
        llm_gateway_enabled: bool = False,
    ) -> None:
        self._db_factory = db_factory
        self._machine_bus = machine_bus
        self._server_url = server_url
        # #124 — Optional MCP template service. Kept optional so
        # tests that only care about the skill library path don't
        # need to wire a secrets key. When None, ``_build_sync_frame``
        # skips the MCP overlay step entirely (no-op for agents that
        # have no instances attached anyway).
        self._mcp_template_service = mcp_template_service
        # #197 — When true, ``_build_sync_frame`` injects
        # ``ANTHROPIC_BASE_URL`` / ``OPENAI_BASE_URL`` (pointing at this
        # server's ``/api/v1/llm`` reverse proxy) plus a sentinel for
        # the agent's own doorae token under ``engine_secrets``. The
        # machine daemon substitutes the sentinel with the live agent
        # token before piping to the agent's stdin, so the server
        # doesn't have to know the plaintext token (which it cannot
        # — only the hash is stored after grant).
        self._llm_gateway_enabled = llm_gateway_enabled

    # ── Public API ──────────────────────────────────────────────

    async def request_start(self, agent_id: str) -> None:
        """Select a machine, bump generation, send ``sync_desired_state``."""
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

    async def request_stop(self, agent_id: str) -> None:
        """Set desired_state='stopped' and push ``sync_desired_state``.

        #219 — also flips ``actual_state`` into the transitional
        ``stopping`` badge immediately so admin UIs don't wait up to
        30 s for the machine's next periodic report. If the agent has
        no machine placement the daemon can't confirm the stop, so
        short-circuit to ``stopped`` here to avoid a permanent
        stuck-stopping row.
        """
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
        if self._mcp_template_service is not None:
            from doorae.mcp_templates.merge import (
                merge_for_engine,
                render_instance,
                settings_path_for_engine,
            )

            settings_path = settings_path_for_engine(agent.engine)
            if settings_path is not None:
                pairs = await self._mcp_template_service.list_instances_for_agent(
                    db, agent.id,
                )
                overlays = []
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
                        engine=agent.engine,
                    )
                    if rendered is not None:
                        overlays.append(rendered)
                if overlays:
                    admin_content = files_map.get(settings_path)
                    files_map[settings_path] = merge_for_engine(
                        engine=agent.engine,
                        admin_content=admin_content,
                        overlays=overlays,
                    )

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

        engine_secrets = self._build_gateway_engine_secrets(agent.engine)

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
            "files": files_map,
            "engine_secrets": engine_secrets,
            "reasoning_effort": agent.reasoning_effort,
            "model": agent.model,
            "sub_rooms": sub_rooms_info,
            "restart_policy": agent.restart_policy,
            "max_restarts": agent.max_restarts,
            "restart_window_seconds": agent.restart_window_seconds,
            # Issue #73 — forward the runtime selector to the machine
            # daemon so it spawns via the right binary path. Pre-#73
            # machines ignore the unknown key and fall back to the
            # SpawnManifest default of ``"python"``.
            "runtime": getattr(agent, "runtime", "python") or "python",
        }

    # Sentinel the machine spawner expands to the agent's own doorae
    # token just before piping engine_secrets to the agent's stdin.
    # Kept as a module-level constant so the machine-side test can
    # import and assert on the exact value rather than duplicating a
    # literal string. See packages/machine/doorae_machine/spawner.py.
    AGENT_TOKEN_SENTINEL = "@DOORAE_AGENT_TOKEN"

    def _build_gateway_engine_secrets(self, engine: str) -> dict[str, str]:
        """Populate ``engine_secrets`` for the LLM gateway when enabled.

        Returns ``{}`` when the feature flag is off so machines running
        against the pre-gateway contract see no behaviour change.
        When on, returns the env vars the per-engine SDK / CLI reads
        for credential discovery — plus the sentinel value
        ``@DOORAE_AGENT_TOKEN`` for the auth header, which the machine
        spawner substitutes with the live plaintext agent token. The
        server can't supply that plaintext itself because tokens are
        stored as argon2 hashes after grant.
        """
        if not self._llm_gateway_enabled:
            return {}

        base = self._http_base_url()
        if not base:
            # Without a reachable HTTP base we can't point agents at the
            # gateway. Leave engine_secrets empty so the agent falls
            # back to whatever it had before the flag got flipped on.
            return {}

        if engine == "claude-code":
            return {
                "ANTHROPIC_BASE_URL": f"{base}/api/v1/llm",
                "ANTHROPIC_AUTH_TOKEN": self.AGENT_TOKEN_SENTINEL,
            }
        if engine == "codex":
            return {
                "OPENAI_BASE_URL": f"{base}/api/v1/llm/v1",
                "OPENAI_API_KEY": self.AGENT_TOKEN_SENTINEL,
            }
        # Engines without a known env-var contract (openhands,
        # deepagents, gemini-cli) keep using their existing host-level
        # credential paths until follow-up wiring lands.
        return {}

    def _http_base_url(self) -> str:
        """Return the gateway's http(s) URL derived from ``_server_url``.

        ``_server_url`` is the WebSocket dial URL the server tells
        daemons to reconnect through (e.g. ``ws://host:8000``). Strip
        the ``ws`` prefix so agents can reach ``/api/v1/llm/*`` over
        plain HTTP.
        """
        url = self._server_url or ""
        if url.startswith("ws://"):
            return "http://" + url[len("ws://"):]
        if url.startswith("wss://"):
            return "https://" + url[len("wss://"):]
        return url


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
) -> int:
    """Mark stalled handlers as ``handler_orphaned``.

    For every ``request_id`` whose earliest ``handler_started`` is
    older than *threshold_sec* and which has no terminal event
    (``handler_finished`` or prior ``handler_orphaned``) yet,
    insert a single ``handler_orphaned`` row.

    Idempotent by construction: the ``HAVING`` clause excludes
    already-orphaned requests. Returns the number of new orphan
    rows written.
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
        new_rows = 0
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
                    details={
                        "room_id": room_id,
                        "started_at": started_at.isoformat()
                        if started_at is not None
                        else None,
                        "threshold_sec": threshold_sec,
                    },
                )
            )
            new_rows += 1
        await db.commit()
        return new_rows
