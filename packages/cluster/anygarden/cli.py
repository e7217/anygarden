"""Click CLI for the anygarden-server."""

from __future__ import annotations

import secrets
from pathlib import Path

import click


@click.group(invoke_without_command=True)
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8000, type=int, help="Bind port")
@click.option("--db", "db_url", default=None, help="Database URL override")
@click.option("--config", "config_path", default=None, type=click.Path(), help="Config file path")
@click.option("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
@click.pass_context
def main(
    ctx: click.Context,
    host: str,
    port: int,
    db_url: str | None,
    config_path: str | None,
    log_level: str,
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
        _run_server(host, port, db_url, log_level)


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
    """Run Alembic migrations to upgrade the database schema."""
    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_command

    db_url = ctx.obj.get("db_url")

    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", "anygarden/db/migrations")
    if db_url:
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    else:
        anygarden_dir = Path.home() / ".anygarden"
        default_url = f"sqlite+aiosqlite:///{anygarden_dir / 'anygarden.db'}"
        alembic_cfg.set_main_option("sqlalchemy.url", default_url)

    alembic_command.upgrade(alembic_cfg, "head")
    click.echo("Migrations applied.")


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


def _run_server(host: str, port: int, db_url: str | None, log_level: str) -> None:
    """Start uvicorn with the configured settings."""
    import uvicorn

    _apply_runtime_env(host, port, db_url, log_level)

    uvicorn.run(
        "anygarden.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=log_level.lower(),
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


if __name__ == "__main__":
    main()
