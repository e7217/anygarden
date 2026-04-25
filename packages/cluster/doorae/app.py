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

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base
from doorae.observability.logging import configure_logging
from doorae.messages.router import router as messages_router
from doorae.rooms.router import router as rooms_router
from doorae.ws.handler import router as ws_router
from doorae.ws.machine_handler import router as machine_ws_router
from doorae.api.v1.machines import router as machines_api_router
from doorae.api.v1.agents import router as agents_api_router
from doorae.api.v1.graph import router as graph_router
from doorae.api.v1.skills import router as skills_api_router
from doorae.api.v1.mcp_templates import router as mcp_templates_router
from doorae.api.v1.projects import router as projects_router
from doorae.api.v1.llm_gateway import router as llm_gateway_admin_router
from doorae.llm_gateway.reverse_proxy import router as llm_proxy_router
from doorae.mcp import router as mcp_rpc_router
from doorae.auth.routes import router as auth_router
from doorae.api.v1.invites import router as invites_router
from doorae.api.v1.saved import router as saved_router
from doorae.api.v1.search import router as search_router
from doorae.api.v1.tasks import router as tasks_router
from doorae.orchestration.rules import (
    CooldownManager,
    GuestRoomAggregateLimiter,
    TypingTracker,
)
from doorae.presence import PresenceService
from doorae.scheduler.machine_bus import MachineBus
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.ws.manager import ConnectionManager


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
        "       cd doorae-server && uv run alembic -c alembic.ini "
        "stamp <revision_id>\n"
        "  3. Run the remaining migrations:\n"
        "       uv run doorae-server migrate\n"
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle."""
    config: DooraeSettings = app.state.config

    # Ensure data directory exists
    db_path = config.db_url.split("///")[-1] if "///" in config.db_url else None
    if db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Persist JWT secret so tokens survive server restarts
    doorae_dir = Path.home() / ".doorae"
    doorae_dir.mkdir(parents=True, exist_ok=True)
    secret_file = doorae_dir / "jwt_secret"
    if not config.jwt_secret:
        if secret_file.exists():
            config.jwt_secret = secret_file.read_text().strip()
        else:
            config.jwt_secret = secrets.token_urlsafe(64)
            secret_file.write_text(config.jwt_secret)
            secret_file.chmod(0o600)

    # Configure structured logging
    configure_logging(config.log_level)

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
        from doorae.skills_library.service import SkillLibraryService
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
    # When the operator hasn't configured ``DOORAE_MCP_SECRETS_KEY``,
    # we fall back to an ephemeral key and log a loud warning — the
    # cluster still boots (so a fresh install without any MCP usage
    # keeps working) but attach/detach of encrypted credentials will
    # surface the mismatch on every restart. Production deployments
    # that actually use MCP credentials must set the key.
    if not getattr(app.state, "mcp_template_service", None):
        from cryptography.fernet import Fernet

        from doorae.mcp_templates.encryption import MCPSecrets
        from doorae.mcp_templates.service import MCPTemplateService

        # Issue #138 — mirror the jwt_secret persistence pattern so
        # local installs don't lose their MCP credentials on every
        # restart.
        #
        # Resolution order:
        #
        # 1. ``config.mcp_secrets_key`` (from ``DOORAE_MCP_SECRETS_KEY``)
        #    — explicit operator configuration wins.
        # 2. ``~/.doorae/mcp_secrets_key`` file — auto-created on first
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
        mcp_key_file = doorae_dir / "mcp_secrets_key"
        resolved_key = config.mcp_secrets_key
        if not resolved_key:
            try:
                if mcp_key_file.exists():
                    resolved_key = mcp_key_file.read_text().strip()
                else:
                    resolved_key = Fernet.generate_key().decode("ascii")
                    mcp_key_file.write_text(resolved_key)
                    mcp_key_file.chmod(0o600)
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
            # gemini-cli) call back into for the doorae self-MCP
            # entry that ``_build_sync_frame`` bakes into spawn frames.
            cluster_external_url=config.cluster_external_url_or_default(),
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

    # v2: No stale agent reset. Machines reconnect and report actual state.
    # Server reconciles via sync_batch on reconnect.
    if not engine_provided:
        from doorae.db.models import Machine as _Machine
        async with app.state.session_factory() as db:
            from sqlalchemy import update
            # Only reset machines to offline — agents are NOT reset.
            await db.execute(
                update(_Machine)
                .where(_Machine.status == "online")
                .values(status="offline")
            )
            await db.commit()
            import structlog
            structlog.get_logger().info("startup.machines_reset_offline")

    # #246 — reconcile on-disk room shared files against the DB. A
    # crash mid-upload can leave a renamed file with no matching row,
    # or a stale ``.tmp/`` remnant. Sweep them on boot so disk usage
    # stays bounded.
    try:
        from doorae.db.models import RoomSharedFile as _RoomSharedFile
        from doorae.rooms import file_storage as _file_storage

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
        from doorae.auth.password import hash_password
        from doorae.db.models import User

        async with app.state.session_factory() as db:
            from sqlalchemy import func
            count = (await db.execute(select(func.count()).select_from(User))).scalar()
            if count == 0:
                db.add(User(
                    email="admin@doorae.dev",
                    password_hash=hash_password("admin"),
                    is_admin=True,
                ))
                await db.commit()
                import structlog
                structlog.get_logger().info("dev.admin_created", email="admin@doorae.dev")

    # #126 — stale-check cron. Default 6h interval so we don't hammer
    # GitHub; override via ``DOORAE_SKILL_STALE_INTERVAL_HOURS``. A
    # value of 0 (or any non-positive) disables the task entirely —
    # used by tests to avoid background I/O. Similarly, the task is
    # skipped when an existing ``skill_stale_task`` is already on
    # app.state (test double).
    stale_task = getattr(app.state, "skill_stale_task", None)
    if stale_task is None:
        interval_hours_env = os.environ.get(
            "DOORAE_SKILL_STALE_INTERVAL_HOURS", "6"
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
        from doorae.llm_gateway.bootstrap import bootstrap_gateway
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
    # ``DOORAE_ORPHAN_SWEEPER_INTERVAL_SEC=0`` (tests) or when a test
    # double has already populated ``app.state.orphan_sweeper_task``.
    orphan_task = getattr(app.state, "orphan_sweeper_task", None)
    if orphan_task is None:
        interval_env = os.environ.get(
            "DOORAE_ORPHAN_SWEEPER_INTERVAL_SEC", "60"
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

    yield

    # #197 — Tear down the gateway before the engine / session factory
    # go away. ``shutdown_gateway`` is safe to call even if bootstrap
    # never ran (no-op when app.state lacks the supervisor).
    from doorae.llm_gateway.bootstrap import shutdown_gateway
    await shutdown_gateway(app)

    # Shutdown: cancel background crons and wait for them to actually
    # stop before the event loop tears down. ``return_exceptions``
    # via the explicit try/except keeps the shutdown path from being
    # poisoned by CancelledError or a late task exception.
    for attr in ("skill_stale_task", "orphan_sweeper_task"):
        task: asyncio.Task | None = getattr(app.state, attr, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
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


async def _run_orphan_sweeper(app: FastAPI, interval_seconds: float) -> None:
    """Periodically promote stuck ``handler_started`` rows to
    ``handler_orphaned``.

    See ``doorae.scheduler.lifecycle.sweep_orphaned_requests`` for
    the semantics. This wrapper only handles scheduling, error
    containment (one bad sweep must not kill the loop), and
    warm-up delay so a freshly-booted server doesn't do DB work in
    the first second of lifespan.
    """
    import structlog

    from doorae.scheduler.lifecycle import sweep_orphaned_requests

    log = structlog.get_logger("orphan_sweeper")

    warmup = min(15.0, interval_seconds)
    try:
        await asyncio.sleep(warmup)
    except asyncio.CancelledError:
        return

    while True:
        try:
            factory = app.state.session_factory
            n = await sweep_orphaned_requests(factory)
            if n:
                log.info("orphan_sweeper.marked", count=n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("orphan_sweeper.error", error=str(exc))

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


def create_app(config: DooraeSettings | None = None) -> FastAPI:
    """Build and return the configured FastAPI application."""
    if config is None:
        config = DooraeSettings()

    app = FastAPI(title="Doorae", version="0.2.0", lifespan=lifespan)
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
    # #197 — LLM gateway reverse proxy + admin CRUD. Both are always
    # included; their handlers 503 when ``app.state.llm_gateway_*``
    # isn't wired (feature flag off) so this is harmless.
    app.include_router(llm_proxy_router)
    app.include_router(llm_gateway_admin_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # SPA static file serving — must be last so API routes take precedence.
    from starlette.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"

    if static_dir.is_dir() and index_html.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="static-assets")

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            file = static_dir / path
            if file.is_file():
                return FileResponse(file)
            return FileResponse(index_html)

    return app
