"""CLI entry point: register, run, status, install-systemd-unit."""

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

import click
import httpx
import structlog

from anygarden_machine.config import MachineConfig, load_token, save_token
from anygarden_machine.daemon import MachineDaemon
from anygarden_machine.detector import detect_engines

log = structlog.get_logger()


@click.group()
def main() -> None:
    """Anygarden Machine Daemon - manages agent subprocesses."""
    structlog.configure(
        processors=[
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )


@main.command()
@click.option("--server", required=True, help="Anygarden server URL (e.g. https://anygarden.example.com)")
@click.option("--name", required=True, help="Human-readable machine name")
def register(server: str, name: str) -> None:
    """Register this machine with a Anygarden server."""
    # Step 1: Authenticate user
    click.echo("Authenticate with Anygarden server:")
    email = click.prompt("  Email")
    password = getpass.getpass("  Password: ")

    base_url = server.rstrip("/")
    try:
        with httpx.Client(timeout=30) as client:
            # Get JWT
            resp = client.post(
                f"{base_url}/api/v1/auth/login",
                json={"email": email, "password": password},
            )
            resp.raise_for_status()
            jwt_token = resp.json()["token"]

            # Step 2: Detect engines
            click.echo("Detecting available engines...")
            result = asyncio.run(detect_engines())
            capabilities = [
                {"engine": e.engine, "version": e.version, "path": e.path}
                for e in result.engines
            ]
            click.echo(f"  Found {len(capabilities)} engine(s)")
            for cap in capabilities:
                click.echo(f"    - {cap['engine']} ({cap['version']})")

            # Step 3: Register machine
            resp = client.post(
                f"{base_url}/api/v1/machines",
                json={
                    "name": name,
                    "capabilities": capabilities,
                },
                headers={"Authorization": f"Bearer {jwt_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            machine_id = data.get("id") or data.get("machine_id")
            machine_token = data["machine_token"]

    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: Server returned {exc.response.status_code}", err=True)
        sys.exit(1)
    except httpx.RequestError as exc:
        click.echo(f"Error: Could not connect to server: {exc}", err=True)
        sys.exit(1)

    # Step 4: Save config and token
    # Determine WS URL from server base
    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url = f"{ws_url}/ws/machines/{machine_id}"

    config = MachineConfig(
        machine_id=machine_id,
        name=name,
        server_url=ws_url,
    )
    config.save()
    save_token(machine_token)

    click.echo(f"Registered! machine_id={machine_id}")
    click.echo(f"Config saved to ~/.anygarden/machine.toml")
    click.echo(f"Token saved to ~/.anygarden/machine.token (chmod 600)")


@main.command()
@click.option("--config", "config_path", type=click.Path(exists=True), default=None,
              help="Config file path (default: ~/.anygarden/machine.toml)")
@click.option("--server", default=None, help="Server WS URL override (e.g. ws://host:8000)")
@click.option("--token", default=None, help="Machine token override (or use ~/.anygarden/machine.token)")
@click.option("--machine-id", default=None, help="Machine ID override")
def run(config_path: str | None, server: str | None, token: str | None, machine_id: str | None) -> None:
    """Run the machine daemon (connects to server via WebSocket)."""
    # Try loading config file, but allow all-CLI usage
    try:
        path = Path(config_path) if config_path else None
        config = MachineConfig.load(path)
    except Exception:
        config = MachineConfig(machine_id="", name="", server_url="")

    file_token = None
    try:
        file_token = load_token()
    except Exception:
        pass

    # CLI overrides
    final_id = machine_id or config.machine_id
    final_server = server or config.server_url
    final_token = token or file_token

    # Build WS URL if HTTP URL given
    if final_server and final_server.startswith("http"):
        final_server = final_server.replace("https://", "wss://").replace("http://", "ws://")
    # Append /ws/machines/{id} if not already present
    if final_server and "/ws/machines/" not in final_server and final_id:
        final_server = f"{final_server.rstrip('/')}/ws/machines/{final_id}"

    if not final_id:
        click.echo("Error: No machine_id. Run 'anygarden-machine register' or pass --machine-id.", err=True)
        sys.exit(1)
    if not final_server:
        click.echo("Error: No server URL. Run 'anygarden-machine register' or pass --server.", err=True)
        sys.exit(1)
    if not final_token:
        click.echo("Error: No token. Run 'anygarden-machine register' or pass --token.", err=True)
        sys.exit(1)

    click.echo(f"Starting daemon: machine_id={final_id}, server={final_server}")
    daemon = MachineDaemon(
        server_url=final_server,
        machine_id=final_id,
        machine_token=final_token,
        labels=config.labels if hasattr(config, 'labels') else {},
    )
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        click.echo("Daemon stopped.")


@main.command()
def status() -> None:
    """Show machine status from the server."""
    config = MachineConfig.load()
    token = load_token()

    if not config.machine_id or not token:
        click.echo("Error: Not registered. Run 'anygarden-machine register' first.", err=True)
        sys.exit(1)

    # Derive HTTP base URL from WS URL
    base_url = config.server_url.replace("wss://", "https://").replace("ws://", "http://")
    base_url = base_url.rsplit("/ws/", 1)[0]

    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{base_url}/api/v1/machines/{config.machine_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        click.echo(f"Error: Server returned {exc.response.status_code}", err=True)
        sys.exit(1)
    except httpx.RequestError as exc:
        click.echo(f"Error: Could not connect: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Machine: {data.get('name', config.name)} ({config.machine_id})")
    click.echo(f"Status:  {data.get('status', 'unknown')}")
    agents = data.get("running_agents", [])
    click.echo(f"Agents:  {len(agents)} running")
    for agent in agents:
        click.echo(f"  - {agent.get('agent_id', '?')} (engine={agent.get('engine', '?')})")


@main.command("install-systemd-unit")
def install_systemd_unit() -> None:
    """Generate and install a systemd user unit file."""
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit_path = unit_dir / "anygarden-machine.service"

    unit_content = f"""\
[Unit]
Description=Anygarden Machine Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={sys.executable} -m anygarden_machine.cli run
Restart=always
RestartSec=5
Environment=PATH={Path(sys.executable).parent}:/usr/bin:/bin

[Install]
WantedBy=default.target
"""

    unit_path.write_text(unit_content)
    click.echo(f"Unit file written to {unit_path}")
    click.echo("Enable and start with:")
    click.echo("  systemctl --user daemon-reload")
    click.echo("  systemctl --user enable --now anygarden-machine")


if __name__ == "__main__":
    main()
