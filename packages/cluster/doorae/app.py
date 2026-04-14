"""FastAPI application factory and lifespan manager."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from sqlalchemy import text

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
from doorae.api.v1.projects import router as projects_router
from doorae.auth.routes import router as auth_router
from doorae.api.v1.saved import router as saved_router
from doorae.api.v1.search import router as search_router
from doorae.api.v1.tasks import router as tasks_router
from doorae.orchestration.rules import CooldownManager, TypingTracker
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
    from sqlalchemy import text

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
    if not getattr(app.state, "agent_lifecycle", None):
        server_url = f"ws://{config.reachable_host()}:{config.port}"
        app.state.agent_lifecycle = AgentLifecycle(
            db_factory=app.state.session_factory,
            machine_bus=app.state.machine_bus,
            server_url=server_url,
        )

    # Initialize WebSocket manager and orchestration singletons on app.state
    # so they are not module-level globals (avoids state leaks in tests and
    # per-worker isolation issues in multi-worker deployments).
    if not getattr(app.state, "connection_manager", None):
        app.state.connection_manager = ConnectionManager()
    if not getattr(app.state, "cooldown_manager", None):
        app.state.cooldown_manager = CooldownManager(capacity=5, refill_rate=1.0)
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

    # Dev mode: auto-create admin user
    if config.dev:
        from doorae.auth.password import hash_password
        from doorae.db.models import User

        async with app.state.session_factory() as db:
            from sqlalchemy import select, func
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

    yield

    # Shutdown: dispose of the engine only if we created it
    if not engine_provided:
        await app.state.engine.dispose()


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
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(saved_router)
    app.include_router(search_router)
    app.include_router(tasks_router)

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
