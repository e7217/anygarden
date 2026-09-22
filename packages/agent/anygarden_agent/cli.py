"""CLI entry points -- ``anygarden-agent`` and ``anygarden-client``."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import click
import structlog

from anygarden_agent import secrets as agent_secrets
from anygarden_agent.auth.token import load_token
from anygarden_agent.integrations import ENGINES, removed_engine_error
from anygarden_agent.profile.loader import load_profile

logger = structlog.get_logger(__name__)


_ENGINE_CHOICES = sorted(ENGINES)


class _EngineChoice(click.Choice):
    def convert(self, value, param, ctx):
        error = removed_engine_error(value)
        if error:
            self.fail(error, param, ctx)
        return super().convert(value, param, ctx)


# #492/#500 — CLI engine names → ``_turn_timeout`` engine keys. The helper
# keys off short names (codex/pi); codex-cli shares
# codex's turn-timeout profile. Every name in ENGINES MUST map to a key the
# ``_turn_timeout`` defaults know, else the agent crashes at spawn when
# ``resolve_turn_timeout`` raises (#500). Regression-tested in test_cli.
_ENGINE_TIMEOUT_KEY: dict[str, str] = {
    "pi-cli": "pi",
    # codex-cli uses the "codex" turn-timeout profile (_ENGINE_DEFAULTS["codex"]);
    # #506 removed the SDK "codex" engine but the timeout key stays for codex-cli.
    "codex-cli": "codex",
}


@click.command("anygarden-agent")
@click.option(
    "--engine",
    required=False,
    default=None,
    type=_EngineChoice(_ENGINE_CHOICES),
    help="LLM engine to use.",
)
@click.option("--name", required=False, default=None, help="Agent display name")
@click.option(
    "--server",
    required=False,
    default=None,
    help="WebSocket server URL (e.g. ws://localhost:8000)",
)
@click.option("--token", default=None, help="Auth token (or set ANYGARDEN_TOKEN)")
@click.option("--room", "rooms", multiple=True, help="Room IDs to join")
@click.option("--provider", default=None, help="Explicit model provider")
@click.option(
    "--endpoint-configured",
    is_flag=True,
    help="Require direct endpoint configuration from private stdin",
)
@click.option("--model", default=None, help="LLM model name override")
@click.option("--system-prompt", default=None, help="System prompt override")
@click.option("--profile", default=None, help="Load agent profile from YAML file")
@click.option(
    "--reasoning-effort", default=None, help="Reasoning effort level (low/medium/high)"
)
def agent_main(
    engine: str | None,
    name: str | None,
    server: str | None,
    token: str | None,
    rooms: tuple[str, ...],
    model: str | None,
    system_prompt: str | None,
    profile: str | None,
    reasoning_effort: str | None,
    provider: str | None = None,
    endpoint_configured: bool = False,
) -> None:
    """Run a Anygarden agent with the specified engine."""
    # Consume engine_secrets piped by the machine daemon over stdin
    # BEFORE any engine setup — keeps API keys out of the agent's
    # ``/proc/self/environ`` while still making them available via
    # ``anygarden_agent.secrets`` for adapters that need them (#184).
    # Safe in interactive dev runs too: ``load_from_stdin`` short-
    # circuits on a tty-backed stdin.
    try:
        agent_secrets.load_from_stdin()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None

    # If --profile is given, load defaults from the YAML profile
    if profile:
        try:
            agent_profile = load_profile(profile)
        except FileNotFoundError as exc:
            click.echo(str(exc), err=True)
            sys.exit(1)
        engine = engine or agent_profile.engine
        name = name or agent_profile.name
        model = model or agent_profile.model or None
        system_prompt = system_prompt or agent_profile.system_prompt
        if not rooms:
            rooms = tuple(agent_profile.rooms)

    error = removed_engine_error(engine)
    if error:
        raise click.ClickException(error)

    # Validate required fields after profile merge
    if not engine:
        click.echo("Error: --engine is required (or specify --profile).", err=True)
        sys.exit(1)
    if not name:
        click.echo("Error: --name is required (or specify --profile).", err=True)
        sys.exit(1)
    if not server:
        click.echo("Error: --server is required.", err=True)
        sys.exit(1)
    if not rooms:
        click.echo(
            "Error: at least one --room is required (or specify --profile).", err=True
        )
        sys.exit(1)

    execution_launch = None
    if engine in {"codex-cli", "pi-cli"} or endpoint_configured:
        from anygarden_agent.runtime.execution.launch import load_execution_launch

        try:
            execution_launch = load_execution_launch(
                engine=engine,
                provider=provider,
                model=model,
                generation=int(os.environ.get("ANYGARDEN_AGENT_GENERATION", "0")),
                endpoint_configured=endpoint_configured,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None

    resolved_token = load_token(cli_token=token)
    asyncio.run(
        _run_agent(
            engine,
            name,
            server,
            resolved_token,
            list(rooms),
            model,
            system_prompt,
            reasoning_effort,
            execution_launch=execution_launch,
        )
    )


async def _run_agent(
    engine: str,
    name: str,
    server: str,
    token: str,
    rooms: list[str],
    model: str | None,
    system_prompt: str | None,
    reasoning_effort: str | None = None,
    execution_launch=None,
) -> None:
    from anygarden_agent.client import ChatClient
    from anygarden_agent.integrations._turn_timeout import (
        resolve_ping_timeout,
        resolve_turn_timeout,
    )

    # #492 — the WS ping_timeout must tolerate the engine's turn timeout or a
    # long turn is silently dropped on keepalive. CLI engine names differ from
    # the helper's engine keys; the mapping lives at module level
    # (``_ENGINE_TIMEOUT_KEY``) so it can be regression-tested (#500).
    engine_key = _ENGINE_TIMEOUT_KEY.get(engine, engine)
    ping_timeout = resolve_ping_timeout(resolve_turn_timeout(engine_key))

    client = ChatClient(
        server,
        token=token,
        agent_name=name,
        ping_timeout=ping_timeout,
        # The machine materializer preserves agent-created files directly
        # under the agent root, so cursors written here survive a respawn
        # and the reconnect replays whatever arrived while we were down.
        state_dir=Path.cwd(),
        execution_launch=execution_launch,
    )

    try:
        await _setup_engine(
            client, engine, name, model, system_prompt, reasoning_effort
        )
        if (
            execution_launch is not None
            and execution_launch.endpoint is not None
            and not client.execution_launch_ready
        ):
            raise click.ClickException(
                "Direct endpoints require the common execution bridge; legacy engine fallback refused"
            )
        for room_id in rooms:
            await client.join_room(room_id)
        click.echo(f"Agent '{name}' running with engine={engine}, rooms={rooms}")
        await client.run()
    finally:
        await client.close()


def _compose_identity_header(name: str | None) -> str | None:
    """Build the self-identity preamble injected into every engine's
    system prompt (#538).

    The runtime knows the agent's ``name`` but pre-#538 never told the
    LLM, so a profile-less agent answered "who are you?" with the engine
    default persona ("Codex") and mis-attributed peers/humans. Stating
    the name (and the no-impersonation rule) up front anchors identity
    regardless of profile richness. Returns ``None`` when no name is
    known so the caller leaves the prompt untouched.
    """
    if not name:
        return None
    return (
        f'You are "{name}", one participant in a multi-agent chat room. '
        "Other participants (humans and agents) appear in the room roster; "
        "refer to them by their display names. "
        "Do not speak as, or on behalf of, another participant."
    )


def _with_identity(name: str | None, system_prompt: str | None) -> str | None:
    """Prepend the identity header (#538) to ``system_prompt``.

    Keeps the existing prompt intact and merely anchors identity above
    it. When no name is known the prompt is returned unchanged so
    behaviour is a strict superset of pre-#538.
    """
    header = _compose_identity_header(name)
    if not header:
        return system_prompt
    if not system_prompt:
        return header
    return f"{header}\n\n{system_prompt}"


async def _setup_engine(
    client: Any,
    engine: str,
    name: str,
    model: str | None,
    system_prompt: str | None,
    reasoning_effort: str | None = None,
) -> None:
    """Lazy-import and wire the chosen engine to the client."""
    if engine in {"codex-cli", "pi-cli"}:
        from anygarden_agent.integrations.room_execution import (
            integrate_with_room_execution,
        )

        await integrate_with_room_execution(
            client,
            engine=engine,
            model=model,
            system_prompt=_with_identity(
                name, system_prompt or "You are a helpful coding assistant."
            ),
            reasoning_effort=reasoning_effort,
        )
    else:
        click.echo(f"Engine '{engine}' is not yet implemented.", err=True)
        sys.exit(1)


@click.command("anygarden-client")
@click.option("--server", required=True, help="WebSocket server URL")
@click.option("--user", required=True, help="User display name")
@click.option("--room", "rooms", multiple=True, required=True, help="Room IDs to join")
@click.option("--token", default=None, help="Auth token (or set ANYGARDEN_TOKEN)")
def client_main(
    server: str,
    user: str,
    rooms: tuple[str, ...],
    token: str | None,
) -> None:
    """Run a text-based chat client."""
    resolved_token = load_token(cli_token=token)
    asyncio.run(_run_client(server, user, list(rooms), resolved_token))


async def _run_client(
    server: str,
    user: str,
    rooms: list[str],
    token: str,
) -> None:
    from anygarden_agent.client import ChatClient

    client = ChatClient(server, token=token, agent_name=user)

    @client.on_message
    async def _print_msg(msg: dict) -> None:
        content = msg.get("content", "")
        pid = msg.get("participant_id", "?")
        room_id = msg.get("room_id", "?")
        print(f"[{room_id}] {pid}: {content}")

    for room_id in rooms:
        await client.join_room(room_id)

    click.echo(f"Connected as '{user}' to rooms: {rooms}")
    click.echo("Type messages and press Enter to send. Ctrl+C to exit.")

    # Run reader and stdin sender concurrently
    async def _stdin_sender() -> None:
        loop = asyncio.get_event_loop()
        while True:
            try:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                line = line.strip()
                if line and rooms:
                    await client.send(rooms[0], line)
            except (EOFError, KeyboardInterrupt):
                break

    try:
        await asyncio.gather(
            client.run(),
            _stdin_sender(),
            return_exceptions=True,
        )
    finally:
        await client.close()


if __name__ == "__main__":
    agent_main()
