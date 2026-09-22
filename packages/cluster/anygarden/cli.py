"""Click CLI for the anygarden-server."""

from __future__ import annotations

import secrets
from pathlib import Path

import click


@click.group(invoke_without_command=True)
@click.option("--host", default=None, help="Bind address (default: 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: 8000)")
@click.option("--db", "db_url", default=None, help="Database URL override")
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="ANYGARDEN_* .env file path",
)
@click.option(
    "--log-level",
    default=None,
    help="Log level (default: INFO; DEBUG, INFO, WARNING, ERROR)",
)
@click.pass_context
def main(
    ctx: click.Context,
    host: str | None,
    port: int | None,
    db_url: str | None,
    config_path: str | None,
    log_level: str | None,
) -> None:
    """Anygarden — lightweight multi-agent chat server."""
    ctx.ensure_object(dict)
    ctx.obj["host"] = host
    ctx.obj["port"] = port
    ctx.obj["db_url"] = db_url
    ctx.obj["log_level"] = log_level
    ctx.obj["config_path"] = config_path

    # Default action: start the server
    if ctx.invoked_subcommand is None:
        _run_server(host, port, db_url, log_level, config_path)


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize the ~/.anygarden/ directory and generate config."""
    anygarden_dir = Path.home() / ".anygarden"
    anygarden_dir.mkdir(parents=True, exist_ok=True)

    config_file = anygarden_dir / "config.env"
    if not config_file.exists():
        jwt_secret = secrets.token_urlsafe(64)
        config_file.write_text(
            f"ANYGARDEN_JWT_SECRET={jwt_secret}\n"
            f"ANYGARDEN_DB_URL=sqlite+aiosqlite:///{anygarden_dir / 'anygarden.db'}\n"
            f"ANYGARDEN_LOG_LEVEL=INFO\n"
        )
        click.echo(f"Created config at {config_file}")
    else:
        click.echo(f"Config already exists at {config_file}")

    click.echo("Initialization complete.")


@main.command()
@click.pass_context
def migrate(ctx: click.Context) -> None:
    """Run Alembic migrations to upgrade the database schema.

    This is the operator's only manual recovery path when the server
    refuses to boot on a schema mismatch, so it has to work from an
    installed package in an arbitrary working directory, and it has to
    target the same database the server would open (#647).
    """
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig
    from sqlalchemy.engine import make_url

    # Go through Settings rather than re-deriving a default, so --config,
    # ~/.anygarden/config.env and ANYGARDEN_DB_URL are all honoured. The
    # previous code read only --db and otherwise hardcoded
    # ~/.anygarden/anygarden.db, silently ignoring the other two.
    config = _load_server_settings(
        None, None, ctx.obj.get("db_url"), None, ctx.obj.get("config_path")
    )

    alembic_cfg = AlembicConfig()
    # Resolve the script location relative to this package, matching
    # app.py's _alembic_action. The previous relative path only resolved
    # when the CWD happened to be the source tree, so an installed
    # anygarden failed with "Path doesn't exist: anygarden/db/migrations".
    script_location = Path(__file__).parent / "db" / "migrations"
    alembic_cfg.set_main_option("script_location", str(script_location))
    alembic_cfg.set_main_option("sqlalchemy.url", config.db_url)

    alembic_command.upgrade(alembic_cfg, "head")
    # Name the target: the bug this replaces was invisible precisely
    # because "Migrations applied." never said what they were applied to.
    click.echo(
        f"Migrations applied to {make_url(config.db_url).render_as_string(hide_password=True)}"
    )


def _apply_runtime_env(
    host: str, port: int, db_url: str | None, log_level: str
) -> None:
    """Reflect CLI-supplied runtime knobs into ``ANYGARDEN_*`` env vars.

    AnygardenSettings reads from the process environment, so any CLI flag that
    should be visible to the app factory has to be promoted before
    ``uvicorn.run`` imports ``anygarden.app``.

    ``--db`` is authoritative over any pre-existing ``ANYGARDEN_DB_URL`` because
    the flag is an explicit override. ``--host`` and ``--port`` are *not*
    authoritative: they describe where uvicorn binds, which is not always
    the same as the address agents should dial back (docker port mapping,
    reverse proxies, k8s services). When the operator has already pointed
    ``ANYGARDEN_HOST`` / ``ANYGARDEN_PORT`` at a dial-back address, we must leave
    it alone — otherwise a perfectly valid ``--host 0.0.0.0`` deployment
    silently rewrites its public hostname to ``0.0.0.0`` and ends up
    unreachable.

    An empty string is treated as unset. Docker compose, ``export FOO=``,
    and most CI shells spell "not meaningfully configured" as ``FOO=""``,
    and in particular pydantic refuses to parse ``""`` as an int so
    leaving ``ANYGARDEN_PORT=""`` in place would crash the server on boot.
    """
    import os

    if db_url:
        os.environ["ANYGARDEN_DB_URL"] = db_url
    if not os.environ.get("ANYGARDEN_LOG_LEVEL"):
        os.environ["ANYGARDEN_LOG_LEVEL"] = log_level
    if not os.environ.get("ANYGARDEN_HOST"):
        os.environ["ANYGARDEN_HOST"] = host
    if not os.environ.get("ANYGARDEN_PORT"):
        os.environ["ANYGARDEN_PORT"] = str(port)


def _load_server_settings(
    host: str | None,
    port: int | None,
    db_url: str | None,
    log_level: str | None,
    config_path: str | None,
):
    """Load the .env config and apply explicitly supplied CLI overrides."""
    from anygarden.config import AnygardenSettings

    resolved_config = Path(config_path) if config_path else None
    default_config = Path.home() / ".anygarden" / "config.env"
    if resolved_config is None and default_config.is_file():
        resolved_config = default_config
    if resolved_config is not None:
        config = AnygardenSettings(  # type: ignore[call-arg]
            _env_file=str(resolved_config)
        )
    else:
        config = AnygardenSettings()
    if host is not None:
        config.host = host
    if port is not None:
        config.port = port
    if db_url is not None:
        config.db_url = db_url
    if log_level is not None:
        config.log_level = log_level
    return config


def _run_server(
    host: str | None,
    port: int | None,
    db_url: str | None,
    log_level: str | None,
    config_path: str | None,
) -> None:
    """Start uvicorn with the configured settings."""
    import uvicorn

    from anygarden.app import create_app

    config = _load_server_settings(host, port, db_url, log_level, config_path)
    _apply_runtime_env(config.host, config.port, config.db_url, config.log_level)

    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        # Issue #190 — codex agents can legitimately hold a turn for
        # 5+ minutes while the SDK waits on tool chains. uvicorn's
        # default ``ws_ping_interval=20, ws_ping_timeout=20`` closes
        # the connection mid-turn from the server side, so the
        # agent's post-turn ``send`` hits a dead socket and the
        # answer is silently lost. These values need to match the
        # client-side keepalive extension in
        # ``anygarden_agent.client.ChatClient._room_loop``.
        ws_ping_interval=60,
        ws_ping_timeout=600,
    )


# ---------------------------------------------------------------------------
# #396 — unified ``anygarden`` dispatcher.
#
# A thin click group that routes ``anygarden <server|machine|agent|client>``
# to the matching component CLI. The heavy component packages are optional
# extras (``anygarden[server]`` / ``[machine]`` / ``[agent]``); each
# subcommand imports its target lazily so the bare ``anygarden`` core stays
# light and a missing extra surfaces an actionable install hint instead of a
# raw ImportError.
# ---------------------------------------------------------------------------


def _load_or_hint(extra: str, import_fn):
    """Import a component CLI callable, or exit with an install hint.

    ``import_fn`` is invoked inside a try/except so an absent optional extra
    (e.g. ``anygarden[machine]`` not installed) maps to a clean
    ``pip install`` instruction rather than a traceback.
    """
    try:
        return import_fn()
    except ImportError as exc:  # optional extra not installed
        raise SystemExit(
            f'"anygarden {extra}" requires the {extra} extra. '
            f'Install it with:\n\n    pip install "anygarden[{extra}]"\n\n'
            f"(import failed: {exc})"
        )


_PASSTHROUGH = {
    "ignore_unknown_options": True,
    "allow_extra_args": True,
    "help_option_names": [],
}


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def dispatch() -> None:
    """anygarden — unified CLI for the server, machine, agent, and client."""


def _server_extra_installed() -> bool:
    """True when the server stack (FastAPI + uvicorn) is importable.

    The server CLI (``main``) lives in this module so it always imports, but
    it only works when the ``[server]`` extra is present. Probing with
    ``find_spec`` lets us emit the same install hint as the other
    subcommands instead of a raw ImportError deep inside ``_run_server``.
    """
    import importlib.util

    return all(
        importlib.util.find_spec(mod) is not None for mod in ("fastapi", "uvicorn")
    )


@dispatch.command(name="server", context_settings=_PASSTHROUGH, add_help_option=False)
@click.pass_context
def _server(ctx: click.Context) -> None:
    """Run the chat server (requires ``anygarden[server]``)."""
    if not _server_extra_installed():
        raise SystemExit(
            '"anygarden server" requires the server extra. '
            'Install it with:\n\n    pip install "anygarden[server]"\n'
        )
    # ``main`` lives in this same module; FastAPI/uvicorn are imported lazily
    # inside ``_run_server`` / ``migrate``.
    main(args=ctx.args, prog_name="anygarden server", standalone_mode=True)


@dispatch.command(name="machine", context_settings=_PASSTHROUGH, add_help_option=False)
@click.pass_context
def _machine(ctx: click.Context) -> None:
    """Run the machine daemon (requires ``anygarden[machine]``)."""
    machine_main = _load_or_hint(
        "machine",
        lambda: __import__("anygarden_machine.cli", fromlist=["main"]).main,
    )
    machine_main(args=ctx.args, prog_name="anygarden machine", standalone_mode=True)


@dispatch.command(name="agent", context_settings=_PASSTHROUGH, add_help_option=False)
@click.pass_context
def _agent(ctx: click.Context) -> None:
    """Run an agent (requires ``anygarden[agent]``)."""
    agent_main = _load_or_hint(
        "agent",
        lambda: __import__("anygarden_agent.cli", fromlist=["agent_main"]).agent_main,
    )
    agent_main(args=ctx.args, prog_name="anygarden agent", standalone_mode=True)


@dispatch.command(name="client", context_settings=_PASSTHROUGH, add_help_option=False)
@click.pass_context
def _client(ctx: click.Context) -> None:
    """Run the interactive client (requires ``anygarden[agent]``)."""
    client_main = _load_or_hint(
        "agent",
        lambda: __import__("anygarden_agent.cli", fromlist=["client_main"]).client_main,
    )
    client_main(args=ctx.args, prog_name="anygarden client", standalone_mode=True)


def deprecated_server_main() -> None:
    """Entry point for the legacy ``anygarden-server`` script (#396).

    Kept for one release so existing systemd units / docs keep working.
    Emits a deprecation notice to stderr, then delegates to the server CLI.
    Routing through ``anygarden server`` does NOT hit this path, so the
    warning only appears for the old command.
    """
    import sys

    print(
        "warning: 'anygarden-server' is deprecated; use 'anygarden server' instead.",
        file=sys.stderr,
    )
    main()


if __name__ == "__main__":
    main()


@dispatch.command(name="start")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=lambda: Path.home() / ".anygarden",
    show_default="~/.anygarden",
)
@click.option("--host", default=None)
@click.option("--port", type=click.IntRange(1, 65535), default=None)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
)
@click.option("--workers", type=click.IntRange(1), default=1, show_default=True)
@click.option(
    "--reload",
    is_flag=True,
    help="Not supported in integrated mode (fails explicitly).",
)
@click.option(
    "--peer-port",
    type=click.IntRange(1, 65535),
    default=None,
    help="Opt-in mTLS federation listener port (#591 boundary; requires existing peer credentials)",
)
@click.option(
    "--peer-host",
    default=None,
    help="Federation listener bind address (default: 0.0.0.0 when --peer-port is given)",
)
def start_node(
    data_dir: Path,
    host: str | None,
    port: int | None,
    config_path: str | None,
    workers: int,
    reload: bool,
    peer_port: int | None,
    peer_host: str | None,
) -> None:
    """Run the API and owned local execution together, in the foreground."""
    import os

    if workers != 1 or os.environ.get("WEB_CONCURRENCY", "1") not in ("", "1"):
        raise click.ClickException(
            "Integrated mode supports exactly one worker; use --workers 1"
        )
    if reload:
        raise click.ClickException(
            "--reload is unsupported in integrated mode; stop then start the node"
        )
    if not _server_extra_installed():
        raise click.ClickException(
            'Install the node with: pip install "anygarden[server,agent]"'
        )
    try:
        import anygarden_agent  # noqa: F401 — installed in the same execution environment
        import uvicorn
        from anygarden.app import create_app
        from anygarden.config import AnygardenSettings
    except ImportError as exc:
        raise click.ClickException(
            'Install the node with: pip install "anygarden[server,agent]"'
        ) from exc
    data_dir = data_dir.absolute()
    if data_dir.is_symlink():
        raise click.ClickException("Node data directory must not be a symlink")
    config_file = config_path or str(data_dir / "config.env")
    config = AnygardenSettings(_env_file=config_file)
    if "db_url" not in config.model_fields_set:
        config.db_url = f"sqlite+aiosqlite:///{data_dir / 'anygarden.db'}"
    if "room_files_dir" not in config.model_fields_set:
        config.room_files_dir = data_dir / "room_files"
    if "artifact_files_dir" not in config.model_fields_set:
        config.artifact_files_dir = data_dir / "artifact_files"
    config.local_node_data_dir = data_dir
    if peer_port is not None:
        peer_dir = config.peer_credentials_dir or (data_dir / "peer")
        cert, key = peer_dir / "peer-cert.pem", peer_dir / "peer-key.pem"
        if not (cert.exists() and key.exists()):
            raise click.ClickException(
                f"--peer-port requires peer credentials; create them first under {peer_dir} "
                "(explicit local setup, startup never generates or rotates them)"
            )
        config.peer_listen_port = peer_port
        config.peer_listen_host = peer_host or "0.0.0.0"
    if host is not None:
        config.host = host
    if port is not None:
        config.port = port
    _apply_runtime_env(config.host, config.port, config.db_url, config.log_level)
    app = create_app(config)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            workers=1,
            log_level=config.log_level.lower(),
            ws_ping_interval=60,
            ws_ping_timeout=600,
        )
    )

    def request_shutdown() -> None:
        server.should_exit = True

    app.state.node_shutdown_callback = request_shutdown
    click.echo(f"Starting AnyGarden node in {data_dir}")
    try:
        server.run()
    except KeyboardInterrupt:
        # Uvicorn's capture_signals() restores the previous SIGINT handler
        # and re-raises the captured signal AFTER graceful shutdown, so a
        # clean Ctrl-C reaches this frame as KeyboardInterrupt once the
        # lifespan already confirmed cleanup. Swallowing it here is safe:
        # the checks below still fail the command when cleanup was NOT
        # confirmed, and letting it escape would print "Aborted!" and exit
        # 1, which a supervising process misreads as a crash to restart.
        pass
    if not server.started:
        raise click.ClickException("Node did not start; see the startup error above")
    # Uvicorn logs lifespan failures instead of propagating them to run().
    # A started server is not evidence that its owned children stopped safely.
    if not getattr(app.state, "node_shutdown_complete", False):
        raise click.ClickException(
            "Node shutdown failed; cleanup was not confirmed and recovery is required"
        )
    if getattr(app.state.local_execution, "failed", False):
        raise click.ClickException("Local execution failed; see the server error above")


@dispatch.command(name="stop")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=lambda: Path.home() / ".anygarden",
    show_default="~/.anygarden",
)
@click.option(
    "--timeout", type=click.FloatRange(min=0.1), default=30.0, show_default=True
)
def stop_local_node(data_dir: Path, timeout: float) -> None:
    """Gracefully stop the current owner of this data directory."""
    from anygarden.node.ownership import NodeOwnershipError, stop_node

    try:
        stop_node(data_dir, timeout=timeout)
    except (NodeOwnershipError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("AnyGarden node stopped; local process cleanup confirmed")
