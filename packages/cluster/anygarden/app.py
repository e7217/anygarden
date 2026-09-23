"""FastAPI application factory and lifespan manager."""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from anygarden_machine.safefs import secure_chmod
from fastapi import FastAPI
from sqlalchemy import select, text

from anygarden.api.v1.agents import router as agents_api_router
from anygarden.api.v1.budgets import router as budgets_router
from anygarden.api.v1.engine_endpoints import (
    probe_router as engine_endpoint_probe_router,
    router as engine_endpoints_router,
)
from anygarden.api.v1.errors import PublicAPIError, public_api_error_handler
from anygarden.api.v1.goals import router as goals_router
from anygarden.api.v1.graph import router as graph_router
from anygarden.api.v1.invites import router as invites_router
from anygarden.api.v1.usage import router as usage_router
from anygarden.api.v1.machines import router as machines_api_router
from anygarden.api.v1.mcp_templates import router as mcp_templates_router
from anygarden.api.v1.projects import router as projects_router
from anygarden.api.v1.saved import router as saved_router
from anygarden.api.v1.search import router as search_router
from anygarden.api.v1.skills import router as skills_api_router
from anygarden.api.v1.system import router as system_router
from anygarden.api.v1.tasks import router as tasks_router
from anygarden.api.v1.turns import router as turns_router
from anygarden.auth.routes import router as auth_router
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import backfill_message_fts, create_message_fts
from anygarden.db.models import Base
from anygarden.mcp import router as mcp_rpc_router
from anygarden.messages.router import router as messages_router
from anygarden.observability.logging import configure_logging
from anygarden.orchestration.rules import (
    CooldownManager,
    GuestRoomAggregateLimiter,
    TypingTracker,
)
from anygarden.presence import PresenceService
from anygarden.rooms.router import router as rooms_router
from anygarden.routing.router import router as routing_router
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.workspaces.router import router as workspaces_router
from anygarden.ws.handler import router as ws_router
from anygarden.ws.machine_handler import router as machine_ws_router
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


async def _repair_failed_upgrade(engine) -> dict | None:
    """Undo the two states that make ``alembic upgrade head`` unrecoverable.

    Returns a description of what was repaired, or ``None`` when there was
    nothing this function knows how to fix — in which case the caller must
    re-raise the original failure rather than retry.

    Both states are SQLite-specific and both were observed together on a
    real database (#646):

    1. **Dangling ``messages_fts`` triggers.** SQLite reparses the *entire*
       schema on ``ALTER TABLE ... RENAME``, and Alembic's batch mode is
       built on temp-table + rename. So triggers left behind by a missing
       FTS virtual table (#520) break batch migrations on completely
       unrelated tables — the reported failure was ``060_message_linked_tasks``
       renaming ``tasks``, which has nothing to do with ``messages``.

    2. **Leftover ``_alembic_tmp_*`` tables.** A failed upgrade is *not*
       rolled back: pysqlite only opens an implicit transaction before DML,
       so the ``CREATE TABLE _alembic_tmp_x`` that opens a batch migration
       lands in autocommit and survives. Batch mode issues a bare
       ``CREATE TABLE``, so the leftover collides on the next attempt.

    Repairing (1) without (2) is therefore not enough for any database that
    has already failed to boot once — which is every database that actually
    hits this bug.

    Only DDL is repaired here. Re-indexing existing rows is deliberately
    left to ``_self_heal_message_fts`` *after* the upgrade succeeds, because
    ``backfill_message_fts`` reads concrete ``messages`` columns and must
    see the final schema, not a mid-migration one.
    """
    if engine.dialect.name != "sqlite":
        return None

    dropped: list[str] = []
    fts_recreated = False

    async with engine.begin() as conn:
        leftovers = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    r"AND name LIKE '\_alembic\_tmp\_%' ESCAPE '\'"
                )
            )
        ).scalars()
        for name in leftovers:
            # Safe to drop unconditionally: batch mode renames the temp
            # table into place on success, so anything still named
            # _alembic_tmp_* is the residue of a failed run. Startup is
            # single-writer (integrated mode pins WEB_CONCURRENCY=1).
            await conn.execute(text(f'DROP TABLE "{name}"'))
            dropped.append(name)

        objects = (
            await conn.execute(
                text(
                    "SELECT type, name FROM sqlite_master "
                    "WHERE name = 'messages' OR name LIKE 'messages_fts%'"
                )
            )
        ).all()
        names = {name for _type, name in objects}
        dangling_triggers = {name for kind, name in objects if kind == "trigger"}
        if dangling_triggers and "messages_fts" not in names and "messages" in names:
            await create_message_fts(conn)
            fts_recreated = True

    if not dropped and not fts_recreated:
        return None
    return {"dropped_tmp_tables": dropped, "fts_recreated": fts_recreated}


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
        try:
            await _alembic_action("upgrade", db_url, "head")
        except Exception as first_error:
            # The upgrade can be blocked by residue that has nothing to do
            # with the migration being applied — see _repair_failed_upgrade.
            # Repair once and retry once; anything else is reported with
            # context so a genuine schema problem is neither hidden behind a
            # retry (#646) nor buried in a bare traceback (#650).
            log.debug("startup.schema_migration_traceback", exc_info=True)
            repaired = await _repair_failed_upgrade(engine)
            if repaired is None:
                raise RuntimeError(
                    _migration_failure_message(
                        db_url,
                        await _read_current_revision(engine),
                        _try_head_revision(),
                        first_error,
                    )
                ) from first_error
            log.warning("startup.schema_upgrade_repaired", **repaired)
            try:
                await _alembic_action("upgrade", db_url, "head")
            except Exception as retry_error:
                log.debug("startup.schema_migration_traceback", exc_info=True)
                raise RuntimeError(
                    _migration_failure_message(
                        db_url,
                        await _read_current_revision(engine),
                        _try_head_revision(),
                        retry_error,
                        repaired=repaired,
                    )
                ) from retry_error
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
            await conn.execute(
                text("""
                CREATE TABLE IF NOT EXISTS alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
            """)
            )
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
    #
    # Name the database in the recovery steps (#647): the old text told the
    # operator to cd into a directory that does not exist and run alembic
    # against alembic.ini, whose URL points somewhere else entirely — so
    # following it would stamp the wrong file. Redact any password first;
    # this string lands in boot logs and bug reports.
    from sqlalchemy.engine import make_url

    safe_url = make_url(db_url).render_as_string(hide_password=True)
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
        "  2. Stamp that revision explicitly, naming this database:\n"
        f"       alembic -x db_url={safe_url} stamp <revision_id>\n"
        "     (run from packages/cluster in a source checkout)\n"
        "  3. Run the remaining migrations:\n"
        "       anygarden server migrate\n"
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


def _innermost_cause(exc: BaseException) -> BaseException:
    """Walk the ``__cause__`` chain to the error that actually happened.

    SQLAlchemy wraps DBAPI errors, so the outermost message is boilerplate
    and the actionable line sits underneath. Guards against a cyclic chain.
    """
    seen = {id(exc)}
    while exc.__cause__ is not None and id(exc.__cause__) not in seen:
        exc = exc.__cause__
        seen.add(id(exc))
    return exc


def _safe_db_label(db_url: str) -> tuple[str, bool]:
    """Return ``(printable_url, is_sqlite)`` with any password removed.

    A startup failure message is the most-copied artefact of a broken boot —
    it lands in logs, issue reports and screenshots. A DSN password in it
    cannot be recalled, so it never gets rendered. An unparseable URL is
    elided rather than echoed, since we cannot know what it contains.
    """
    try:
        from sqlalchemy.engine import make_url

        url = make_url(db_url)
        return url.render_as_string(hide_password=True), url.drivername.startswith(
            "sqlite"
        )
    except Exception:
        return "(unparseable database URL)", False


def _migration_failure_message(
    db_url: str,
    current_rev: str | None,
    head_rev: str | None,
    cause: BaseException,
    repaired: dict | None = None,
) -> str:
    """Compose the operator-facing text for a failed ``upgrade head`` (#650).

    Case 3 (legacy unstamped) and the integrated-node schema guard already
    explain themselves; the Case 1 upgrade failure was the one path that
    surfaced as a bare traceback. Every lookup here is failure-tolerant:
    building the diagnostic must never be the thing that fails.
    """
    label, is_sqlite = _safe_db_label(db_url)
    lines = [
        "Schema migration failed.",
        f"  database:         {label}",
        f"  current revision: {current_rev or 'unknown'}",
        f"  target:           head ({head_rev or 'unknown'})",
        f"  cause:            {_innermost_cause(cause)}",
    ]

    if repaired:
        done = []
        if repaired.get("dropped_tmp_tables"):
            done.append("dropped " + ", ".join(repaired["dropped_tmp_tables"]))
        if repaired.get("fts_recreated"):
            done.append("recreated messages_fts")
        if done:
            lines.append(
                f"  repaired first:   {'; '.join(done)} — the retry still failed"
            )

    lines.append("")
    if is_sqlite:
        # Established by experiment, not assumed: pysqlite only opens an
        # implicit transaction before DML, so a failed batch migration rolls
        # back its data changes but leaves its scratch table committed.
        lines.append(
            "The alembic_version marker above is unchanged, but SQLite does not "
            "roll back DDL: a partially applied batch migration can leave "
            "_alembic_tmp_* tables behind."
        )
        lines.append("")
    lines.extend(
        [
            "To investigate:",
            "  1. Back up the database before any manual repair.",
            "  2. Inspect the migrations between the two revisions above.",
            "  3. Re-run with --log-level DEBUG for the full traceback.",
        ]
    )
    return "\n".join(lines)


async def _read_current_revision(engine) -> str | None:
    """Best-effort ``alembic_version`` read; ``None`` when unavailable."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            revisions = list(result.scalars())
        return ", ".join(revisions) or None
    except Exception:
        return None


def _try_head_revision() -> str | None:
    """Best-effort head lookup; ``None`` when the versions dir is unreadable."""
    try:
        return _discover_head_revision()
    except Exception:
        return None


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


async def _mark_removed_engine_agents(db) -> list[str]:
    """Explain retired engines without rewriting their configuration or history."""
    from anygarden.db.models import Agent
    from anygarden.engines.validation import ENGINE_REMOVED_MESSAGE, REMOVED_ENGINES
    from anygarden.scheduler.lifecycle import _mark_unavailable

    agents = (
        (await db.execute(select(Agent).where(Agent.engine.in_(REMOVED_ENGINES))))
        .scalars()
        .all()
    )
    for agent in agents:
        agent.last_crash_reason = ENGINE_REMOVED_MESSAGE
        _mark_unavailable(agent, "engine_removed", {"engine": agent.engine})
    return [agent.id for agent in agents]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own local execution outside API startup, including partial failures."""
    from anygarden.node.ownership import NodeOwner

    owner = None
    backend = None
    listener = None
    engine_provided = getattr(app.state, "engine", None) is not None
    if app.state.config.local_node_data_dir is not None:
        if os.environ.get("WEB_CONCURRENCY", "1") not in ("", "1"):
            raise RuntimeError("Integrated node mode supports exactly one API worker")
        owner = NodeOwner(app.state.config.local_node_data_dir)
        owner.acquire()  # Before schema/startup writes or any child process.
        app.state.local_machine_id = owner.identity["machine_id"]
        app.state.node_owner = owner
        app.state.node_shutdown_complete = False
    clean = False
    try:
        await _startup_server(app)
        if owner is not None:
            from anygarden.node.execution import LocalExecutionBackend

            backend = LocalExecutionBackend(app, owner)
            app.state.local_execution = backend
            await backend.start()
            owner.write_state("running")
        else:
            # #649 — without an integrated node this process serves the API
            # only; agents must run on a separate machine daemon. Say so once,
            # so `make dev` users do not read silence as a working setup. Kept
            # at info: for a multi-host deployment this is the normal state.
            # Emitted after _startup_server so structlog is already configured.
            import structlog

            structlog.get_logger().info(
                "startup.local_execution_disabled",
                reason=(
                    "not integrated mode; run `anygarden start` to execute "
                    "agents in this process, or attach a machine daemon"
                ),
            )
        if app.state.config.peer_listen_port is not None:
            # #594 real-machine tier — opt-in federation listener. Fails the
            # startup when services did not compose (missing/invalid peer
            # credentials): an explicitly requested listener must not silently
            # run disabled. Never started without the explicit option (#591).
            if app.state.peer_service is None:
                raise RuntimeError(
                    "--peer-port was requested but peer services did not compose; "
                    "check the peer credential files under the node data directory"
                )
            from anygarden.federation.transport import PeerListener

            listener = PeerListener(
                app.state.peer_service,
                host=app.state.config.peer_listen_host or "0.0.0.0",
                port=app.state.config.peer_listen_port,
                channel_service=app.state.channel_service,
            )
            await listener.start()
            app.state.peer_listener = listener
        yield
    finally:
        try:
            try:
                try:
                    if listener is not None:
                        await listener.stop()
                finally:
                    if backend is not None:
                        await backend.close()
            finally:
                await _shutdown_server(app, engine_provided)
            clean = True
        finally:
            if owner is not None:
                owner.release(clean=clean)
                app.state.node_shutdown_complete = clean


def _compose_federation_services(app: FastAPI) -> None:
    """Attach PeerService/ChannelService when persisted credentials exist.

    #593 product-app wiring. Node identity is created only by the explicit
    ``federation.certificates.create_credentials`` setup step — startup
    never writes, overwrites, or rotates credential files, and never starts
    the mTLS listener. When both ``peer-cert.pem`` and ``peer-key.pem`` are
    present the PeerService is constructed from the certificate's node_id
    and the app's session factory, and a ChannelService is attached unless
    the caller already injected one via ``create_app(channel_service=...)``.
    Absent credentials leave every node/shared-channel route mounted but
    disabled (PEERING_DISABLED / SHARING_DISABLED 503); partial credential
    sets log a warning and stay disabled rather than crashing boot.
    """
    config: AnygardenSettings = app.state.config
    if getattr(app.state, "session_factory", None) is None:
        return
    anygarden_dir = config.local_node_data_dir or (Path.home() / ".anygarden")
    peer_dir = config.peer_credentials_dir or (anygarden_dir / "peer")
    cert_path = peer_dir / "peer-cert.pem"
    key_path = peer_dir / "peer-key.pem"
    cert_exists, key_exists = cert_path.exists(), key_path.exists()
    if not cert_exists and not key_exists:
        return
    if not (cert_exists and key_exists):
        import structlog

        structlog.get_logger("federation").warning(
            "peer_credentials.partial",
            directory=str(peer_dir),
            cert=cert_exists,
            key=key_exists,
        )
        return
    from anygarden.federation.certificates import inspect_certificate
    from anygarden.federation.delegation_wiring import make_local_policy
    from anygarden.federation.service import PeerService
    from anygarden.shared_channels.service import ChannelService

    try:
        identity = inspect_certificate(cert_path.read_text())
        peers = PeerService(
            node_id=identity.node_id,
            cert_path=cert_path,
            key_path=key_path,
            sessions=app.state.session_factory,
            local_policy=make_local_policy(identity.node_id),
        )
        app.state.federation_node_id = identity.node_id
    except Exception as exc:
        # P4 (task #40 review): an unreadable/expired/mismatched credential
        # pair must not crash boot — same fail-closed posture as partial or
        # absent credentials: routes stay mounted but disabled, the operator
        # sees why, and no child surface starts half-configured.
        import structlog

        structlog.get_logger("federation").error(
            "peer_credentials.invalid",
            directory=str(peer_dir),
            error=str(exc),
        )
        return
    app.state.peer_service = peers
    if getattr(app.state, "channel_service", None) is None:
        app.state.channel_service = ChannelService(
            node_id=peers.node_id,
            peers=peers,
            sessions=peers.sessions,
        )
        # task #51 — product delegation coordinator: guards/submitters/
        # projections ride on the composed channel service (all roles on
        # every node; each checks authority_node_id itself). Explicit
        # create_app(channel_service=...) injections stay untouched.
        from anygarden.federation.delegation_wiring import install_product_delegation

        app.state.delegation_service = install_product_delegation(
            app.state.channel_service
        )


async def _startup_server(app: FastAPI) -> None:
    """Initialize API state; teardown is handled even if startup raises."""
    config: AnygardenSettings = app.state.config

    # Ensure data directory exists
    db_path = config.db_url.split("///")[-1] if "///" in config.db_url else None
    if db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Persist JWT secret so tokens survive server restarts
    anygarden_dir = config.local_node_data_dir or (Path.home() / ".anygarden")
    anygarden_dir.mkdir(parents=True, exist_ok=True)
    secret_file = anygarden_dir / "jwt_secret"
    if not config.jwt_secret:
        if secret_file.exists():
            config.jwt_secret = secret_file.read_text().strip()
        else:
            config.jwt_secret = secrets.token_urlsafe(64)
            secret_file.write_text(config.jwt_secret)
            secure_chmod(secret_file, 0o600)

    # Workspace invocation audits use a domain-separated HMAC derived from
    # the persisted server secret. Prompt content is never stored.
    from anygarden.workspaces.service import configure_audit_key

    configure_audit_key(config.jwt_secret)

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
        if config.local_node_data_dir is not None:
            async with engine.connect() as conn:
                try:
                    result = await conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    )
                    revisions = list(result.scalars())
                except Exception:
                    revisions = None  # Fresh/legacy classification stays in _ensure_schema_ready.
            if revisions is not None and revisions != [_discover_head_revision()]:
                raise RuntimeError(
                    "Integrated node requires the current database schema. Back up the database "
                    "and explicitly migrate it before using anygarden start."
                )
        await _ensure_schema_ready(engine, config.db_url)

    # #593 — compose federation services from persisted credentials. No-op
    # (routes stay mounted but disabled, 503) when credentials are absent.
    _compose_federation_services(app)

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
            resolved_key,
            dev_mode=config.dev,
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
        app.state.connection_manager.set_presence_service(app.state.presence_service)
    if not getattr(app.state, "cooldown_manager", None):
        app.state.cooldown_manager = CooldownManager(capacity=5, refill_rate=1.0)
    # Guests get a stricter bucket — §11.7 of the design doc. The two
    # managers are intentionally separate instances so a single
    # burst from a chatty registered user doesn't starve the guest
    # bucket and vice versa.
    if not getattr(app.state, "guest_cooldown_manager", None):
        app.state.guest_cooldown_manager = CooldownManager(capacity=3, refill_rate=0.5)
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
    # Retired engines retain their saved state and receive migration guidance.
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
            removed_engine_ids = await _mark_removed_engine_agents(db)
            await db.commit()
            import structlog

            logger = structlog.get_logger()
            logger.info("startup.machines_reset_offline")
            if removed_engine_ids:
                logger.info(
                    "startup.removed_engines_blocked",
                    count=len(removed_engine_ids),
                    agent_ids=removed_engine_ids,
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

            structlog.get_logger().info("startup.room_files_cleanup", removed=removed)
    except Exception:  # pragma: no cover — best-effort boot chore
        import structlog

        structlog.get_logger().exception("startup.room_files_cleanup_failed")

    # Dev mode: auto-create admin user
    if config.dev:
        from anygarden.auth.password import hash_password
        from anygarden.db.models import User

        async with app.state.session_factory() as db:
            from sqlalchemy import func

            count = (await db.execute(select(func.count()).select_from(User))).scalar()
            if count == 0:
                db.add(
                    User(
                        email="admin@anygarden.dev",
                        password_hash=hash_password("admin"),
                        is_admin=True,
                    )
                )
                await db.commit()
                import structlog

                structlog.get_logger().info(
                    "dev.admin_created", email="admin@anygarden.dev"
                )

    # #126 — stale-check cron. Default 6h interval so we don't hammer
    # GitHub; override via ``ANYGARDEN_SKILL_STALE_INTERVAL_HOURS``. A
    # value of 0 (or any non-positive) disables the task entirely —
    # used by tests to avoid background I/O. Similarly, the task is
    # skipped when an existing ``skill_stale_task`` is already on
    # app.state (test double).
    stale_task = getattr(app.state, "skill_stale_task", None)
    if stale_task is None:
        interval_hours_env = os.environ.get("ANYGARDEN_SKILL_STALE_INTERVAL_HOURS", "6")
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

    # #204 — orphan sweeper. Writes ``handler_orphaned`` rows when a
    # ``handler_started`` has no matching ``handler_finished`` after
    # 20 min (engine_timeout 15 min + 5 min slack). Disabled when
    # ``ANYGARDEN_ORPHAN_SWEEPER_INTERVAL_SEC=0`` (tests) or when a test
    # double has already populated ``app.state.orphan_sweeper_task``.
    orphan_task = getattr(app.state, "orphan_sweeper_task", None)
    if orphan_task is None:
        interval_env = os.environ.get("ANYGARDEN_ORPHAN_SWEEPER_INTERVAL_SEC", "60")
        try:
            orphan_interval = float(interval_env)
        except ValueError:
            orphan_interval = 60.0
        if orphan_interval > 0:
            app.state.orphan_sweeper_task = asyncio.create_task(
                _run_orphan_sweeper(app, orphan_interval),
                name="orphan_sweeper",
            )

    # Transactional turn outbox + lease recovery. Tests may disable it with 0.
    if getattr(app.state, "turn_recovery_task", None) is None:
        try:
            turn_interval = float(
                os.environ.get("ANYGARDEN_TURN_RECOVERY_INTERVAL_SEC", "1")
            )
        except ValueError:
            turn_interval = 1.0
        if turn_interval > 0:
            app.state.turn_recovery_task = asyncio.create_task(
                _run_turn_recovery(app, turn_interval),
                name="turn_recovery",
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

    # #66 — delegation pickup sweeper. Finalizes ``requested`` delegations
    # whose executor never accepted within the pickup window, as channel-admin
    # cancels (dev01's #58 sweeper). Requires the federation composition
    # (peer credentials); disabled when
    # ``ANYGARDEN_DELEGATION_SWEEPER_INTERVAL_SEC=0`` or when a test double
    # has already populated ``app.state.delegation_sweeper_task``.
    delegation_task = getattr(app.state, "delegation_sweeper_task", None)
    if (
        delegation_task is None
        and getattr(app.state, "delegation_service", None) is not None
        and getattr(app.state, "channel_service", None) is not None
    ):
        try:
            delegation_interval = float(
                os.environ.get("ANYGARDEN_DELEGATION_SWEEPER_INTERVAL_SEC", "60")
            )
        except ValueError:
            delegation_interval = 60.0
        try:
            pickup_timeout = float(
                os.environ.get("ANYGARDEN_DELEGATION_PICKUP_TIMEOUT_SEC", "3600")
            )
        except ValueError:
            pickup_timeout = 3600.0
        if delegation_interval > 0:
            app.state.delegation_sweeper_task = asyncio.create_task(
                _run_delegation_sweeper(app, delegation_interval, pickup_timeout),
                name="delegation_sweeper",
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


async def _shutdown_server(app: FastAPI, engine_provided: bool) -> None:
    # Shutdown: cancel background crons and wait for them to actually
    # stop before the event loop tears down. ``return_exceptions``
    # via the explicit try/except keeps the shutdown path from being
    # poisoned by CancelledError or a late task exception.
    for attr in (
        "skill_stale_task",
        "orphan_sweeper_task",
        "turn_recovery_task",
        "span_reaper_task",
        "delegation_sweeper_task",
    ):
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

    if not engine_provided and getattr(app.state, "engine", None) is not None:
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


async def _run_delegation_sweeper(
    app: FastAPI, interval_seconds: float, pickup_timeout_seconds: float
) -> None:
    """Periodically finalize pickup-timeout delegations (task #66, #54).

    Delegates to ``DelegationService.sweep_pickup_timeouts`` with the node's
    earliest admin as the channel-admin actor; one bad sweep must not kill
    the loop, and the node admin must exist before anything runs.
    """
    from datetime import UTC, datetime, timedelta

    import structlog

    from anygarden.db.models import User

    log = structlog.get_logger("delegation_sweeper")

    await asyncio.sleep(min(15.0, interval_seconds))
    while True:
        try:
            service = getattr(app.state, "delegation_service", None)
            channel_service = getattr(app.state, "channel_service", None)
            if service is not None and channel_service is not None:
                async with channel_service.sessions() as db:
                    admin_id = await db.scalar(
                        select(User.id)
                        .where(User.is_admin.is_(True))
                        .order_by(User.created_at)
                        .limit(1)
                    )
                if admin_id is not None:
                    actor = {
                        "node_id": channel_service.node_id,
                        "kind": "human",
                        "principal_id": admin_id,
                    }
                    result = await service.sweep_pickup_timeouts(
                        channel_service,
                        actor=actor,
                        now=datetime.now(UTC),
                        timeout=timedelta(seconds=pickup_timeout_seconds),
                    )
                    if result.get("finalized") or result.get("skipped"):
                        log.info(
                            "delegation_sweeper.swept",
                            finalized=len(result.get("finalized", [])),
                            skipped=len(result.get("skipped", [])),
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("delegation_sweeper.error", error=str(exc))
        await asyncio.sleep(interval_seconds)


async def _run_turn_recovery(app: FastAPI, interval_seconds: float) -> None:
    """Flush durable dispatches, fence dead leases, and release drains."""

    import structlog
    from sqlalchemy import func, select

    from anygarden.db.models import Agent, AgentTurn
    from anygarden.observability.metrics import durable_turns_by_state
    from anygarden.turns.service import (
        cancel_invalid_turns,
        deliver_pending_outbox,
        recover_stalled_turns,
    )
    from anygarden.workspaces.lifecycle import revoke_invalid_attachments

    log = structlog.get_logger("turn_recovery")
    # Avoid racing the lifespan's own startup transactions (especially the
    # single-connection in-memory SQLite used by tests and local dev).
    try:
        await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        return
    while True:
        try:
            factory = app.state.session_factory
            manager = getattr(app.state, "connection_manager", None)
            lifecycle = getattr(app.state, "agent_lifecycle", None)
            machine_bus = getattr(app.state, "machine_bus", None)
            if lifecycle is not None and machine_bus is not None:
                await revoke_invalid_attachments(factory, machine_bus, lifecycle)
            await cancel_invalid_turns(factory)
            if manager is not None:
                await deliver_pending_outbox(factory, manager)
            recovered = await recover_stalled_turns(factory, manager)
            async with factory() as db:
                pending_agents = set(
                    (
                        await db.scalars(
                            select(Agent.id).where(Agent.pending_generation.isnot(None))
                        )
                    ).all()
                )
                state_rows = (
                    await db.execute(
                        select(AgentTurn.state, func.count()).group_by(AgentTurn.state)
                    )
                ).all()
            durable_turns_by_state.clear()
            for state, count in state_rows:
                durable_turns_by_state.labels(state=state).set(count)
            pending_agents.update(recovered.drain_agents or set())
            if lifecycle is not None:
                for agent_id in pending_agents:
                    await lifecycle.release_generation_drain(agent_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("turn_recovery.error", error=str(exc))
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


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


def create_app(
    config: AnygardenSettings | None = None, *, channel_service=None
) -> FastAPI:
    """Build and return the configured FastAPI application."""
    if config is None:
        config = AnygardenSettings()

    app = FastAPI(title="Anygarden", version="0.2.0", lifespan=lifespan)
    app.add_exception_handler(PublicAPIError, public_api_error_handler)
    app.state.config = config
    app.include_router(ws_router)
    app.include_router(machine_ws_router)
    from anygarden.federation.router import mount_admin
    from anygarden.shared_channels.router import mount_local

    # Node admin routes are always mounted so operators get an explicit
    # PEERING_DISABLED 503 instead of a bare 404; ``_startup_server`` attaches
    # the real PeerService when persisted credentials exist.
    mount_admin(app)
    mount_local(app, channel_service)
    app.include_router(rooms_router)
    app.include_router(messages_router)
    app.include_router(machines_api_router)
    app.include_router(agents_api_router)
    app.include_router(engine_endpoints_router)
    app.include_router(engine_endpoint_probe_router)
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
    app.include_router(turns_router)
    app.include_router(workspaces_router)
    app.include_router(goals_router)
    app.include_router(system_router)
    app.include_router(routing_router)
    # Engine-neutral usage aggregation preserves historical rows.
    app.include_router(usage_router)
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
        the wiring it already owns (DB connectivity and background tasks). Returns 200
        for ``ok``/``degraded`` and 503 only when a *critical*
        dependency is down (DB unreachable).
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
    from starlette.responses import FileResponse, Response
    from starlette.staticfiles import StaticFiles

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"

    if static_dir.is_dir() and index_html.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=static_dir / "assets"),
            name="static-assets",
        )

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            # Removed/unknown API routes are never client-side page routes.
            if path == "api" or path.startswith("api/"):
                return Response(status_code=404)
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
