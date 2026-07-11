"""FastAPI application factory and lifespan manager."""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import select, text

from anygarden_machine.safefs import secure_chmod

from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import backfill_message_fts, create_message_fts
from anygarden.db.models import Base
from anygarden.observability.logging import configure_logging
from anygarden.messages.router import router as messages_router
from anygarden.rooms.router import router as rooms_router
from anygarden.ws.handler import router as ws_router
from anygarden.ws.machine_handler import router as machine_ws_router
from anygarden.api.v1.machines import router as machines_api_router
from anygarden.api.v1.agents import router as agents_api_router
from anygarden.api.v1.graph import router as graph_router
from anygarden.api.v1.skills import router as skills_api_router
from anygarden.api.v1.mcp_templates import router as mcp_templates_router
from anygarden.api.v1.projects import router as projects_router
from anygarden.api.v1.llm_gateway import router as llm_gateway_admin_router
from anygarden.api.v1.budgets import router as budgets_router
from anygarden.llm_gateway.reverse_proxy import router as llm_proxy_router
from anygarden.mcp import router as mcp_rpc_router
from anygarden.auth.routes import router as auth_router
from anygarden.api.v1.invites import router as invites_router
from anygarden.api.v1.saved import router as saved_router
from anygarden.api.v1.search import router as search_router
from anygarden.api.v1.tasks import router as tasks_router
from anygarden.api.v1.goals import router as goals_router
from anygarden.routing.router import router as routing_router
from anygarden.orchestration.rules import (
    CooldownManager,
    GuestRoomAggregateLimiter,
    TypingTracker,
)
from anygarden.presence import PresenceService
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.ws.manager import ConnectionManager


_APP_TABLES = (
    "projects",
    "rooms",
    "users",
    "agents",
    "machines",
    "participants",
    "messages",
    "agent_tokens",
    "machine_engines",
    "machine_tokens",
)


def _is_asset_like_path(path: str) -> bool:
    """True if *path* looks like a static asset request (#473).

    The SPA catch-all should only return ``index.html`` for client-side
    routes, which never carry a file extension (e.g. ``/rooms/abc``). A
    request whose final path segment contains a ``.`` (``favicon.ico``,
    ``robots.txt``) is an asset request — if it didn't match a real file,
    it must 404 rather than fall through to the HTML shell. Only the
    basename matters, so a dot in an earlier segment is ignored.
    """
    return "." in path.rsplit("/", 1)[-1]


async def _self_heal_message_fts(engine) -> None:
    """Ensure ``messages_fts`` exists and is populated on any SQLite DB.

    Migration 008 created the FTS table, but migrations are frozen and
    ``alembic upgrade head`` is a no-op once a DB is stamped past 008.
    So an Alembic-managed DB (Case 1) that predates 008 — or that lost
    the virtual table — stays permanently broken, every search 503ing on
    a missing ``messages_fts`` (#520). ``create_message_fts`` is
    idempotent and ``backfill_message_fts`` only inserts missing rows, so
    running both on every boot is safe. FTS5 is SQLite-only.
    """
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        await create_message_fts(conn)
        await backfill_message_fts(conn)


async def _ensure_schema_ready(engine, db_url: str) -> None:
    """Ensure the database schema is ready, using Alembic where possible.

    Three distinct cases:

    1. **Alembic-managed DB** (``alembic_version`` table exists) → run
       ``alembic upgrade head``. Standard production path.
    2. **Fresh DB** (no ``alembic_version``, no application tables) →
       ``create_all`` then ``stamp head``. Bootstraps a new install and
       puts it under Alembic management.
    3. **Legacy unstamped DB** (no ``alembic_version`` but application
       tables already exist) → **refuse to boot**. We cannot know which
       revision the existing schema matches, and blindly stamping head
       would falsely claim every migration has been applied (producing
       subtle runtime errors when the schema is actually stale). Require
       an operator to baseline the DB explicitly.
    """
    import structlog

    log = structlog.get_logger()

    async with engine.connect() as conn:
        try:
            await conn.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
            has_alembic = True
        except Exception:
            has_alembic = False

        existing_tables: set[str] = set()
        if not has_alembic:
            # Probe each known application table with SELECT ... LIMIT 0.
            # Anything that doesn't error is part of the live schema.
            for table in _APP_TABLES:
                try:
                    await conn.execute(text(f"SELECT 1 FROM {table} LIMIT 0"))
                    existing_tables.add(table)
                except Exception:
                    pass

    if has_alembic:
        await _alembic_action("upgrade", db_url, "head")
        log.info("startup.schema_migrated", action="upgrade", target="head")
        # Self-heal a missing/empty FTS index on an existing DB — `upgrade
        # head` won't recreate the table once stamped past migration 008
        # (#520). No-op when the index is already present and populated.
        await _self_heal_message_fts(engine)
        return

    if not existing_tables:
        # Case 2: truly fresh database.
        #
        # Do create_all and the alembic_version insert inside a SINGLE
        # transaction so the operation is retry-safe: if the process is
        # killed between create_all and the stamp, SQLite rolls the whole
        # thing back and the next boot starts over in Case 2 instead of
        # getting trapped in Case 3 (legacy unstamped — half-materialised
        # tables with no alembic_version row).
        head_rev = _discover_head_revision()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # create_all builds the ORM-declared tables but NOT the raw
            # FTS5 virtual table + triggers (those live only in migration
            # 008, which this bootstrap path never replays). Add them in
            # the same transaction so a fresh install can serve search
            # instead of 500ing on a missing messages_fts (#473). FTS5 is
            # SQLite-only.
            if engine.dialect.name == "sqlite":
                await create_message_fts(conn)
            # Mirror what Alembic's `stamp head` does, inline, so it joins
            # the same transaction as create_all. Schema of alembic_version
            # is Alembic's standard single-column table.
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """))
            await conn.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": head_rev},
            )
        log.info(
            "startup.schema_stamped",
            action="create_all+stamp_atomic",
            target=head_rev,
        )
        return

    # Case 3: legacy DB with data but no Alembic marker — refuse to boot
    # so the operator is forced to make an explicit, auditable decision.
    raise RuntimeError(
        "Database contains application tables but no alembic_version "
        "row — this is a legacy unstamped database. Refusing to boot "
        "because automatically stamping HEAD could claim migrations "
        "have been applied when they have not, leaving the schema "
        "silently out of date.\n"
        "\n"
        "To resolve:\n"
        "  1. Determine which Alembic revision your current schema "
        "matches (check messages.participant_id nullability to "
        "distinguish pre- vs post-004).\n"
        "  2. Stamp that revision explicitly:\n"
        "       cd anygarden-server && uv run alembic -c alembic.ini "
        "stamp <revision_id>\n"
        "  3. Run the remaining migrations:\n"
        "       uv run anygarden-server migrate\n"
        "  4. Restart the server.\n"
        "\n"
        f"Detected application tables: {sorted(existing_tables)}"
    )


def _discover_head_revision() -> str:
    """Return the Alembic head revision id by reading the versions directory.

    Does not touch the database — used by the fresh-DB bootstrap path to
    write the correct ``alembic_version`` row inside the create_all
    transaction without needing a separate Alembic command invocation.
    """
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    cfg = AlembicConfig()
    script_location = Path(__file__).parent / "db" / "migrations"
    cfg.set_main_option("script_location", str(script_location))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("No Alembic head revision found in versions/")
    return head


async def _alembic_action(action: str, db_url: str, target: str) -> None:
    """Run an Alembic command in a thread (Alembic API is sync)."""
    import asyncio
    from pathlib import Path

    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig

    def _run() -> None:
        cfg = AlembicConfig()
        # Resolve the script location relative to this package so it works
        # both when installed and when running from source.
        script_location = Path(__file__).parent / "db" / "migrations"
        cfg.set_main_option("script_location", str(script_location))
        cfg.set_main_option("sqlalchemy.url", db_url)
        if action == "upgrade":
            alembic_command.upgrade(cfg, target)
        elif action == "stamp":
            alembic_command.stamp(cfg, target)
        else:
            raise ValueError(f"Unknown Alembic action: {action}")

    await asyncio.to_thread(_run)


async def _reset_openhands_agents_for_restart(db) -> list[str]:
    """Flip active openhands agents into the orphan state on cluster boot.

    Issue #379 — the openhands engine is an in-process SDK adapter; its
    per-room ``Conversation`` cache lives in agent-process memory and
    is lost whenever the agent process restarts. The CLI-based engines
    (claude-code, codex, gemini-cli) spawn a fresh subprocess per
    session so they boot cleanly, but openhands agents would otherwise
    require a manual stop/start to recover. By setting
    ``actual_state='pending'`` and clearing ``placed_on_machine_id``,
    the standard machine-reconnect path (``_place_orphaned_agents``
    in ``ws/machine_handler.py``) picks them up and triggers a fresh
    ``request_start`` with a bumped generation.

    Returns the agent IDs that were reset, so callers can log them.
    """
    from anygarden.db.models import Agent

    result = await db.execute(
        select(Agent).where(
            Agent.engine == "openhands",
            Agent.actual_state.in_(("running", "starting", "stopping")),
        )
    )
    agents = result.scalars().all()
    for agent in agents:
        agent.actual_state = "pending"
        agent.desired_state = "running"
        agent.pid = None
        agent.placed_on_machine_id = None
    return [a.id for a in agents]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle."""
    config: AnygardenSettings = app.state.config

    # Ensure data directory exists
    db_path = config.db_url.split("///")[-1] if "///" in config.db_url else None
    if db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Persist JWT secret so tokens survive server restarts
    anygarden_dir = Path.home() / ".anygarden"
    anygarden_dir.mkdir(parents=True, exist_ok=True)
    secret_file = anygarden_dir / "jwt_secret"
    if not config.jwt_secret:
        if secret_file.exists():
            config.jwt_secret = secret_file.read_text().strip()
        else:
            config.jwt_secret = secrets.token_urlsafe(64)
            secret_file.write_text(config.jwt_secret)
            secure_chmod(secret_file, 0o600)

    # Configure structured logging
    configure_logging(config.log_level, dev=config.dev)

    # #420 — OpenTelemetry tracing. No-op unless ANYGARDEN_OTEL_ENABLED
    # and an OTLP endpoint are set, so the default boot is unchanged.
    # Tests may pre-set ``app.state.tracing`` to inject a span exporter.
    if not getattr(app.state, "tracing", None):
        from anygarden.observability.tracing import TracingService, setup_tracing

        provider = setup_tracing(config)
        app.state.tracer_provider = provider
        app.state.tracing = TracingService(
            provider,
            capture_content=config.otel_llm_capture_content,
            capture_max_chars=config.otel_llm_capture_max_chars,
        )

    # If engine/session_factory were pre-set (e.g. by tests), reuse them.
    engine_provided = getattr(app.state, "engine", None) is not None
    if not engine_provided:
        engine = build_engine(config.db_url)
        app.state.engine = engine
        app.state.session_factory = build_session_factory(engine)

        # Schema management: if the DB has an alembic_version table, run
        # Alembic upgrade (prod flow). Otherwise fall back to create_all
        # and stamp head — this covers both fresh dev databases and
        # pre-Alembic legacy DBs, bringing them under Alembic control so
        # subsequent migrations apply cleanly.
        await _ensure_schema_ready(engine, config.db_url)

    # Initialize scheduler components (only if not already set by tests)
    if not getattr(app.state, "machine_bus", None):
        app.state.machine_bus = MachineBus()
    # #119 — SkillLibraryService with the default (network-backed)
    # GitHubFetcher. Tests may pre-populate app.state with a service
    # wired to a fake fetcher so register() stays offline.
    if not getattr(app.state, "skill_library_service", None):
        from anygarden.skills_library.service import SkillLibraryService
        app.state.skill_library_service = SkillLibraryService(
            app.state.session_factory,
        )
    # #126 — in-memory stale-check cache shared between the cron loop
    # (writer) and the API layer (reader). Stored on app.state so tests
    # that drive the API without the cron can still seed values.
    if not getattr(app.state, "skill_stale_cache", None):
        app.state.skill_stale_cache = {}
    if not getattr(app.state, "skill_search_cache", None):
        app.state.skill_search_cache = {}
    # #124 — MCPTemplateService + Fernet-backed secrets. Wired
    # BEFORE AgentLifecycle so we can inject it into the lifecycle
    # constructor; lifecycle skips the overlay step when the service
    # is absent, so tests that don't care about MCP can pre-set
    # ``app.state.agent_lifecycle`` without wiring a key.
    #
    # When the operator hasn't configured ``ANYGARDEN_MCP_SECRETS_KEY``,
    # we fall back to an ephemeral key and log a loud warning — the
    # cluster still boots (so a fresh install without any MCP usage
    # keeps working) but attach/detach of encrypted credentials will
    # surface the mismatch on every restart. Production deployments
    # that actually use MCP credentials must set the key.
    if not getattr(app.state, "mcp_template_service", None):
        from cryptography.fernet import Fernet

        from anygarden.mcp_templates.encryption import MCPSecrets
        from anygarden.mcp_templates.service import MCPTemplateService

        # Issue #138 — mirror the jwt_secret persistence pattern so
        # local installs don't lose their MCP credentials on every
        # restart.
        #
        # Resolution order:
        #
        # 1. ``config.mcp_secrets_key`` (from ``ANYGARDEN_MCP_SECRETS_KEY``)
        #    — explicit operator configuration wins.
        # 2. ``~/.anygarden/mcp_secrets_key`` file — auto-created on first
        #    boot, reused on subsequent boots. 0o600 so the key stays
        #    readable only by the server process's user.
        # 3. ``from_config_key(dev_mode=config.dev)`` fallback. In dev
        #    this generates an ephemeral key (and logs a warning); in
        #    prod it raises ``MCPSecretsUnavailable`` so the operator
        #    sees the problem immediately instead of on the first MCP
        #    tool call after a restart.
        #
        # The file write is wrapped in ``try/except`` so a locked-down
        # HOME (e.g. a containerized run with no writable user home)
        # degrades to the ``from_config_key`` fallback instead of
        # crashing.
        mcp_key_file = anygarden_dir / "mcp_secrets_key"
        resolved_key = config.mcp_secrets_key
        if not resolved_key:
            try:
                if mcp_key_file.exists():
                    resolved_key = mcp_key_file.read_text().strip()
                else:
                    resolved_key = Fernet.generate_key().decode("ascii")
                    mcp_key_file.write_text(resolved_key)
                    secure_chmod(mcp_key_file, 0o600)
            except OSError:
                # Can't read or write the persistence file — let the
                # configured fallback handle it based on ``dev`` mode.
                resolved_key = ""

        # ``dev_mode=config.dev`` — prod boot refuses a missing key.
        # Tests default to ``dev=False`` but always pre-set
        # ``mcp_secrets_key`` in conftest, so this path stays green
        # for them.
        # Name this ``mcp_secrets`` (not ``secrets``) to avoid
        # shadowing the stdlib ``secrets`` module imported at the
        # top of the file.
        mcp_secrets = MCPSecrets.from_config_key(
            resolved_key, dev_mode=config.dev,
        )
        app.state.mcp_template_service = MCPTemplateService(
            app.state.session_factory,
            secrets=mcp_secrets,
        )
        # Idempotent builtin seed on every boot so templates stay
        # in sync with the code (new builtin → one restart away).
        await app.state.mcp_template_service.seed_builtins()
    if not getattr(app.state, "agent_lifecycle", None):
        app.state.agent_lifecycle = AgentLifecycle(
            db_factory=app.state.session_factory,
            machine_bus=app.state.machine_bus,
            mcp_template_service=app.state.mcp_template_service,
            # #255 — lifecycle backfills room shared files when an agent
            # transitions into ``running`` (respawn path). The same
            # directory the /rooms/{id}/files upload route writes into.
            room_files_dir=config.room_files_dir,
            # #277 — URL the agent CLI tools (claude-code / codex /
            # gemini-cli) call back into for the anygarden self-MCP
            # entry that ``_build_sync_frame`` bakes into spawn frames.
            cluster_external_url=config.cluster_external_url_or_default(),
            # #359 — gateway feature flag. When on, ``_build_sync_frame``
            # populates ``engine_secrets`` with OPENAI_BASE_URL +
            # OPENAI_API_KEY for openhands agents so they route through
            # the anygarden LLM gateway.
            llm_gateway_enabled=config.llm_gateway_enabled,
        )

    # Initialize WebSocket manager and orchestration singletons on app.state
    # so they are not module-level globals (avoids state leaks in tests and
    # per-worker isolation issues in multi-worker deployments).
    if not getattr(app.state, "connection_manager", None):
        app.state.connection_manager = ConnectionManager()
    # Wire PresenceService (#54) — single source of truth for agent
    # liveness. The setter pattern keeps ConnectionManager free of
    # direct presence imports so we don't introduce a cycle.
    if not getattr(app.state, "presence_service", None):
        app.state.presence_service = PresenceService(app.state.connection_manager)
        app.state.connection_manager.set_presence_service(
            app.state.presence_service
        )
    if not getattr(app.state, "cooldown_manager", None):
        app.state.cooldown_manager = CooldownManager(capacity=5, refill_rate=1.0)
    # Guests get a stricter bucket — §11.7 of the design doc. The two
    # managers are intentionally separate instances so a single
    # burst from a chatty registered user doesn't starve the guest
    # bucket and vice versa.
    if not getattr(app.state, "guest_cooldown_manager", None):
        app.state.guest_cooldown_manager = CooldownManager(
            capacity=3, refill_rate=0.5
        )
    # Room-wide cap on combined guest mentions — blunts LLM-cost
    # amplification when an invite is shared widely. 20 agent-mention
    # events per minute per room is the §11.7 starting point.
    if not getattr(app.state, "guest_room_limiter", None):
        app.state.guest_room_limiter = GuestRoomAggregateLimiter(
            capacity=20, window_seconds=60.0
        )
    if not getattr(app.state, "typing_tracker", None):
        app.state.typing_tracker = TypingTracker(ttl_seconds=5.0)
    # Issue #279 — per-room peer-mention budget. Resets on every
    # human/guest send so the cap applies to a single user turn.
    if not getattr(app.state, "peer_handoff_budget", None):
        from anygarden.orchestration.rules import PeerHandoffBudget

        app.state.peer_handoff_budget = PeerHandoffBudget()

    # v2: No stale agent reset. Machines reconnect and report actual state.
    # Server reconciles via sync_batch on reconnect.
    # Issue #379 — openhands is the lone exception: its in-process SDK
    # state can't survive a process restart, so we proactively flip
    # those agents into the orphan state and let the standard
    # machine-reconnect respawn path bring them back fresh.
    if not engine_provided:
        from anygarden.db.models import Machine as _Machine
        async with app.state.session_factory() as db:
            from sqlalchemy import update
            # Only reset machines to offline — agents are NOT reset.
            await db.execute(
                update(_Machine)
                .where(_Machine.status == "online")
                .values(status="offline")
            )
            reset_openhands_ids = await _reset_openhands_agents_for_restart(db)
            await db.commit()
            import structlog
            logger = structlog.get_logger()
            logger.info("startup.machines_reset_offline")
            if reset_openhands_ids:
                logger.info(
                    "startup.openhands_agents_reset",
                    count=len(reset_openhands_ids),
                    agent_ids=reset_openhands_ids,
                )

    # #246 — reconcile on-disk room shared files against the DB. A
    # crash mid-upload can leave a renamed file with no matching row,
    # or a stale ``.tmp/`` remnant. Sweep them on boot so disk usage
    # stays bounded.
    try:
        from anygarden.db.models import RoomSharedFile as _RoomSharedFile
        from anygarden.rooms import file_storage as _file_storage

        async with app.state.session_factory() as _db:
            known_ids = set(
                (await _db.execute(select(_RoomSharedFile.id))).scalars().all()
            )
        removed = _file_storage.cleanup_orphans(
            config.room_files_dir, known_ids=known_ids
        )
        if removed:
            import structlog
            structlog.get_logger().info(
                "startup.room_files_cleanup", removed=removed
            )
    except Exception:  # pragma: no cover — best-effort boot chore
        import structlog
        structlog.get_logger().exception(
            "startup.room_files_cleanup_failed"
        )

    # Dev mode: auto-create admin user
    if config.dev:
        from anygarden.auth.password import hash_password
        from anygarden.db.models import User

        async with app.state.session_factory() as db:
            from sqlalchemy import func
            count = (await db.execute(select(func.count()).select_from(User))).scalar()
            if count == 0:
                db.add(User(
                    email="admin@anygarden.dev",
                    password_hash=hash_password("admin"),
                    is_admin=True,
                ))
                await db.commit()
                import structlog
                structlog.get_logger().info("dev.admin_created", email="admin@anygarden.dev")

    # #126 — stale-check cron. Default 6h interval so we don't hammer
    # GitHub; override via ``ANYGARDEN_SKILL_STALE_INTERVAL_HOURS``. A
    # value of 0 (or any non-positive) disables the task entirely —
    # used by tests to avoid background I/O. Similarly, the task is
    # skipped when an existing ``skill_stale_task`` is already on
    # app.state (test double).
    stale_task = getattr(app.state, "skill_stale_task", None)
    if stale_task is None:
        interval_hours_env = os.environ.get(
            "ANYGARDEN_SKILL_STALE_INTERVAL_HOURS", "6"
        )
        try:
            interval_hours = float(interval_hours_env)
        except ValueError:
            interval_hours = 6.0
        if interval_hours > 0:
            interval_seconds = interval_hours * 3600.0
            app.state.skill_stale_task = asyncio.create_task(
                _run_skill_stale_cron(app, interval_seconds),
                name="skill_stale_cron",
            )

    # #197 — Bootstrap the embedded LLM gateway (optional). Pre-wired
    # supervisor on app.state (e.g. tests) wins — if something is
    # already there we leave it alone.
    if (
        config.llm_gateway_enabled
        and getattr(app.state, "llm_gateway_supervisor", None) is None
    ):
        from anygarden.llm_gateway.bootstrap import bootstrap_gateway
        # MCPSecrets was already built above for MCP templates; the
        # llm_gateway reuses the same Fernet key per ADR-004. Fetch it
        # from the service (which stores it as ``_secrets``) so this
        # works both when we built the service here and when a test
        # pre-set ``app.state.mcp_template_service``.
        gateway_secrets = getattr(
            app.state.mcp_template_service, "_secrets", None
        )
        if gateway_secrets is None:
            import structlog
            structlog.get_logger().warning(
                "llm_gateway.bootstrap_skipped",
                reason="mcp_template_service has no _secrets attribute",
            )
        else:
            try:
                await bootstrap_gateway(
                    app,
                    config,
                    app.state.session_factory,
                    gateway_secrets,
                )
            except Exception as exc:  # noqa: BLE001
                import structlog
                structlog.get_logger().warning(
                    "llm_gateway.bootstrap_failed",
                    error=str(exc),
                )

    # #204 — orphan sweeper. Writes ``handler_orphaned`` rows when a
    # ``handler_started`` has no matching ``handler_finished`` after
    # 20 min (engine_timeout 15 min + 5 min slack). Disabled when
    # ``ANYGARDEN_ORPHAN_SWEEPER_INTERVAL_SEC=0`` (tests) or when a test
    # double has already populated ``app.state.orphan_sweeper_task``.
    orphan_task = getattr(app.state, "orphan_sweeper_task", None)
    if orphan_task is None:
        interval_env = os.environ.get(
            "ANYGARDEN_ORPHAN_SWEEPER_INTERVAL_SEC", "60"
        )
        try:
            orphan_interval = float(interval_env)
        except ValueError:
            orphan_interval = 60.0
        if orphan_interval > 0:
            app.state.orphan_sweeper_task = asyncio.create_task(
                _run_orphan_sweeper(app, orphan_interval),
                name="orphan_sweeper",
            )

    # #420 — span reaper: ends spans for requests whose terminal
    # lifecycle event never arrived (a lost frame), bounding the
    # in-memory span registry. Only runs when tracing is enabled.
    tracing = getattr(app.state, "tracing", None)
    if (
        getattr(app.state, "span_reaper_task", None) is None
        and tracing is not None
        and tracing.enabled
    ):
        app.state.span_reaper_task = asyncio.create_task(
            _run_span_reaper(app, interval_seconds=60.0, ttl_seconds=1200.0),
            name="span_reaper",
        )

    # #302 — autonomous responsibility (Goal) scheduler. Single
    # in-process polling loop; multi-replica coordination lands in
    # Phase 3 with PostgreSQL advisory locks. Tests may pre-set
    # ``app.state.goal_scheduler`` to a stub to bypass the timer.
    if not getattr(app.state, "goal_scheduler", None):
        from anygarden.goals.scheduler import GoalScheduler

        # #314 — pass the live ConnectionManager so scheduler-fired
        # task assignment messages actually reach the agent's WS
        # session. Without this the synthetic mention is persisted but
        # never broadcast, and the agent never wakes.
        app.state.goal_scheduler = GoalScheduler(
            app.state.session_factory,
            manager=getattr(app.state, "connection_manager", None),
        )
    if hasattr(app.state.goal_scheduler, "start"):
        app.state.goal_scheduler.start()

    yield

    # #197 — Tear down the gateway before the engine / session factory
    # go away. ``shutdown_gateway`` is safe to call even if bootstrap
    # never ran (no-op when app.state lacks the supervisor).
    from anygarden.llm_gateway.bootstrap import shutdown_gateway
    await shutdown_gateway(app)

    # Shutdown: cancel background crons and wait for them to actually
    # stop before the event loop tears down. ``return_exceptions``
    # via the explicit try/except keeps the shutdown path from being
    # poisoned by CancelledError or a late task exception.
    for attr in ("skill_stale_task", "orphan_sweeper_task", "span_reaper_task"):
        task: asyncio.Task | None = getattr(app.state, attr, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    # #420 — flush buffered spans then close the tracer provider so the
    # BatchSpanProcessor's queue isn't dropped on shutdown.
    tracing = getattr(app.state, "tracing", None)
    if tracing is not None:
        tracing.shutdown()
    provider = getattr(app.state, "tracer_provider", None)
    if provider is not None and hasattr(provider, "shutdown"):
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001
            pass

    # #302 — stop the goal scheduler. ``stop`` is idempotent and
    # safe to call when no scheduler ever started.
    scheduler = getattr(app.state, "goal_scheduler", None)
    if scheduler is not None and hasattr(scheduler, "stop"):
        try:
            await scheduler.stop()
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    if not engine_provided:
        await app.state.engine.dispose()


async def _run_skill_stale_cron(app: FastAPI, interval_seconds: float) -> None:
    """Periodically refresh the stale-check cache on ``app.state``.

    First sweep fires immediately after a short warm-up so a fresh
    boot doesn't show "nothing stale, nothing checked" for hours; then
    sleeps for ``interval_seconds`` between each sweep.

    Any per-sweep exception is logged and swallowed — the loop stays
    alive so a transient GitHub outage doesn't permanently disable the
    stale badge.
    """
    import structlog

    log = structlog.get_logger("skill_library.stale_cron")

    # Tiny warm-up delay so the task doesn't fight the rest of lifespan
    # for the event loop right at boot; configurable implicitly via the
    # interval itself but 15s is a good fixed floor.
    warmup = min(15.0, interval_seconds)
    try:
        await asyncio.sleep(warmup)
    except asyncio.CancelledError:
        return

    while True:
        try:
            service = getattr(app.state, "skill_library_service", None)
            if service is not None:
                results = await service.check_all_stale()
                cache = app.state.skill_stale_cache
                # Replace wholesale rather than merge — a row that
                # dropped out of ``check_all_stale`` (was deleted) must
                # disappear from the cache too, else the UI keeps
                # flagging zombie skills as stale.
                cache.clear()
                cache.update(results)
                log.info(
                    "skill_library.stale_swept",
                    total=len(results),
                    stale=sum(1 for r in results.values() if r.stale),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("skill_library.stale_sweep_error", error=str(exc))

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


async def _reconcile_agents_by_state(app: FastAPI) -> None:
    """#427 — refresh the ``agents_by_state`` gauge from a COUNT GROUP BY.

    The gauge was defined but never updated (scraped a permanent 0).
    Reconciling on the orphan-sweeper cadence (~60s) is cheap and gives
    fleet-health panels real data without wiring every state transition.
    """
    from sqlalchemy import func, select

    from anygarden.db.models import Agent
    from anygarden.observability.metrics import agents_by_state

    try:
        async with app.state.session_factory() as db:
            rows = (
                await db.execute(
                    select(Agent.actual_state, func.count()).group_by(
                        Agent.actual_state
                    )
                )
            ).all()
        # Clear stale label series first so a state that dropped to zero
        # doesn't linger at its last value.
        agents_by_state.clear()
        for state, count in rows:
            if state:
                agents_by_state.labels(state=state).set(count)
    except Exception:  # noqa: BLE001 — metric refresh must not break the loop
        pass


async def _run_orphan_sweeper(app: FastAPI, interval_seconds: float) -> None:
    """Periodically promote stuck ``handler_started`` rows to
    ``handler_orphaned``.

    See ``anygarden.scheduler.lifecycle.sweep_orphaned_requests`` for
    the semantics. This wrapper only handles scheduling, error
    containment (one bad sweep must not kill the loop), and
    warm-up delay so a freshly-booted server doesn't do DB work in
    the first second of lifespan.
    """
    import structlog

    from anygarden.observability.metrics import (
        agent_turns_orphaned_total,
        agents_crashed_by_sweep_total,
    )
    from anygarden.scheduler.lifecycle import (
        ORPHAN_THRESHOLD_SEC_DEFAULT,
        notify_and_redispatch_orphans,
        sweep_orphaned_requests,
        sweep_stale_agents,
    )

    log = structlog.get_logger("orphan_sweeper")

    # #447 — stale-heartbeat reaper threshold. ``0`` disables the agent
    # reaper entirely (the orphaned-request sweep below still runs).
    try:
        stale_sec = int(os.environ.get("ANYGARDEN_HEARTBEAT_STALE_SEC", "120"))
    except ValueError:
        stale_sec = 120

    # #481 — slow-path orphan threshold, overridable from the env. The
    # fast path (crashed agent) ignores it regardless.
    try:
        liveness_sec = int(
            os.environ.get(
                "ANYGARDEN_REQUEST_LIVENESS_SEC",
                str(ORPHAN_THRESHOLD_SEC_DEFAULT),
            )
        )
    except ValueError:
        liveness_sec = ORPHAN_THRESHOLD_SEC_DEFAULT

    warmup = min(15.0, interval_seconds)
    try:
        await asyncio.sleep(warmup)
    except asyncio.CancelledError:
        return

    while True:
        try:
            factory = app.state.session_factory
            # #447/#481 — reap dead agents FIRST so the orphan sweep that
            # follows in the same cycle sees their just-``crashed`` state and
            # takes the fast path (no ~20 min wait).
            if stale_sec > 0:
                crashed = await sweep_stale_agents(factory, threshold_sec=stale_sec)
                if crashed:
                    log.info("orphan_sweeper.heartbeat_stale", count=crashed)
                    agents_crashed_by_sweep_total.inc(crashed)
            # #427/#481 — sweep returns the newly-orphaned requests.
            orphaned = await sweep_orphaned_requests(
                factory, threshold_sec=liveness_sec
            )
            if orphaned:
                log.info("orphan_sweeper.marked", count=len(orphaned))
                agent_turns_orphaned_total.inc(len(orphaned))
                # Bridge the DB decision to the in-memory span reaper so
                # the two orphan mechanisms agree immediately (#427).
                tracing = getattr(app.state, "tracing", None)
                if tracing is not None:
                    for orphan in orphaned:
                        tracing.reap_request(orphan.request_id)
                # #481 — surface (room notice) + recover (Task re-dispatch).
                # ``connection_manager`` may be missing in stripped-down test
                # apps; ``notify_and_redispatch_orphans`` degrades gracefully
                # (notice skipped, re-dispatch still attempted).
                manager = getattr(app.state, "connection_manager", None)
                await notify_and_redispatch_orphans(factory, manager, orphaned)
            # #427 — refresh the fleet-health gauge (previously dead).
            await _reconcile_agents_by_state(app)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("orphan_sweeper.error", error=str(exc))

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


async def _run_span_reaper(
    app: FastAPI, *, interval_seconds: float, ttl_seconds: float
) -> None:
    """Periodically reap request traces whose terminal event never came.

    Mirrors ``_run_orphan_sweeper`` but operates on the in-memory span
    registry rather than the DB: a lost ``handler_finished`` /
    ``response_sent`` frame would otherwise leak the live spans forever.
    """
    import structlog

    log = structlog.get_logger("span_reaper")

    try:
        await asyncio.sleep(min(15.0, interval_seconds))
    except asyncio.CancelledError:
        return

    while True:
        try:
            tracing = getattr(app.state, "tracing", None)
            n = tracing.reap(ttl_seconds) if tracing is not None else 0
            if n:
                log.info("span_reaper.reaped", count=n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("span_reaper.error", error=str(exc))

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


def create_app(config: AnygardenSettings | None = None) -> FastAPI:
    """Build and return the configured FastAPI application."""
    if config is None:
        config = AnygardenSettings()

    app = FastAPI(title="Anygarden", version="0.2.0", lifespan=lifespan)
    app.state.config = config
    app.include_router(ws_router)
    app.include_router(machine_ws_router)
    app.include_router(rooms_router)
    app.include_router(messages_router)
    app.include_router(machines_api_router)
    app.include_router(agents_api_router)
    app.include_router(graph_router)
    app.include_router(skills_api_router)
    app.include_router(mcp_templates_router)
    app.include_router(mcp_rpc_router)
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(invites_router)
    app.include_router(saved_router)
    app.include_router(search_router)
    app.include_router(tasks_router)
    app.include_router(goals_router)
    app.include_router(routing_router)
    # #197 — LLM gateway reverse proxy + admin CRUD. Both are always
    # included; their handlers 503 when ``app.state.llm_gateway_*``
    # isn't wired (feature flag off) so this is harmless.
    app.include_router(llm_proxy_router)
    app.include_router(llm_gateway_admin_router)
    # #453 — token-budget policy admin CRUD. The gate these policies
    # drive defaults OFF (hard_stop_enabled=False), so registering this
    # router cannot change runtime behaviour until an admin enables a
    # policy.
    app.include_router(budgets_router)

    # #420 — expose the Prometheus metrics defined in
    # ``observability.metrics`` (previously defined but never scrapeable
    # because no endpoint mounted them). Unauthenticated by design —
    # operators are expected to gate ``/metrics`` at the reverse proxy.
    from prometheus_client import make_asgi_app

    app.mount("/metrics", make_asgi_app())

    @app.get("/healthz")
    async def healthz():
        """Liveness/readiness probe with real dependency checks.

        The server is a switchboard, not a brain — this only inspects
        the wiring it already owns (DB connectivity, the LLM gateway
        supervisor, and the long-running background crons). Returns 200
        for ``ok``/``degraded`` and 503 only when a *critical*
        dependency is down (DB unreachable, or gateway ``FAILED``).
        Components that are intentionally off (feature flag disabled,
        cron interval 0, tracing off) report ``disabled`` and never
        flip the overall status.
        """
        from starlette.responses import JSONResponse

        components: dict[str, str] = {}
        critical_down = False
        degraded = False

        # ── DB: SELECT 1 with a short deadline so a hung pool can't
        # wedge the probe. Unreachable DB is a critical failure. ──
        session_factory = getattr(app.state, "session_factory", None)
        if session_factory is None:
            components["db"] = "disabled"
        else:
            async def _ping_db() -> None:
                async with session_factory() as db:
                    await db.execute(text("SELECT 1"))

            try:
                await asyncio.wait_for(_ping_db(), timeout=2.0)
                components["db"] = "ok"
            except Exception:  # noqa: BLE001 — any failure ⇒ unhealthy
                components["db"] = "unhealthy"
                critical_down = True

        # ── LLM gateway: read the supervisor's state. FAILED is a hard
        # failure (no path back without operator action); CRASHED is a
        # transient self-healing state ⇒ degraded but still serving.
        # Supervisor is None when the gateway flag is off ⇒ disabled. ──
        supervisor = getattr(app.state, "llm_gateway_supervisor", None)
        if supervisor is None:
            components["gateway"] = "disabled"
        else:
            from anygarden.llm_gateway.supervisor import GatewayState

            gw_state = supervisor.state
            if gw_state == GatewayState.FAILED:
                components["gateway"] = "unhealthy"
                critical_down = True
            elif gw_state == GatewayState.CRASHED:
                components["gateway"] = "degraded"
                degraded = True
            else:
                components["gateway"] = "ok"

        # ── Background crons: each is conditionally created and may be
        # None when disabled. A task that exists but has already
        # finished (crashed out of its loop) is unhealthy; None means
        # intentionally off, not a failure. These are non-critical ⇒
        # degraded, never 503. ──
        for label, attr in (
            ("orphan_sweeper", "orphan_sweeper_task"),
            ("span_reaper", "span_reaper_task"),
            ("goal_scheduler", "goal_scheduler"),
        ):
            obj = getattr(app.state, attr, None)
            if obj is None:
                components[label] = "disabled"
                continue
            # ``orphan_sweeper_task`` / ``span_reaper_task`` are raw
            # asyncio.Tasks; ``goal_scheduler`` is a GoalScheduler that
            # wraps its loop in ``._task`` (None until ``start()``).
            task = obj if hasattr(obj, "done") else getattr(obj, "_task", None)
            if task is None:
                # Scheduler object exists but its loop never started.
                components[label] = "disabled"
                continue
            done = getattr(task, "done", None)
            if callable(done) and done():
                components[label] = "unhealthy"
                degraded = True
            else:
                components[label] = "ok"

        if critical_down:
            status = "unhealthy"
            code = 503
        elif degraded:
            status = "degraded"
            code = 200
        else:
            status = "ok"
            code = 200

        return JSONResponse(
            status_code=code,
            content={"status": status, "components": components},
        )

    # SPA static file serving — must be last so API routes take precedence.
    from starlette.staticfiles import StaticFiles
    from starlette.responses import FileResponse, Response

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"

    if static_dir.is_dir() and index_html.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="static-assets")

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            file = static_dir / path
            if file.is_file():
                return FileResponse(file)
            # Asset-like requests (favicon.ico, robots.txt, …) that didn't
            # resolve to a real file must 404 instead of falling through to
            # the HTML shell (#473) — otherwise the browser tries to parse
            # index.html as the asset. SPA routes carry no extension.
            if _is_asset_like_path(path):
                return Response(status_code=404)
            return FileResponse(index_html)

    return app
