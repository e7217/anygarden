"""Let a node also serve another server as one of its machines (#848).

The admin pastes a machine connection code issued by the other server. The
node stores it under ``<data-dir>/remote-machines/<machine-id>/`` (the same
layout as ``anygarden machine run --data-dir``) and runs an ordinary
:class:`MachineDaemon` for it inside the node process. The daemon dials the
other server, which then tells it which agents to run, exactly like a
standalone remote machine. Each link owns its own agents directory and holds
its lock, so it never touches the node's own agents.

A link starts and stops with the node and never accepts the remote server's
self-update: the package is the node's, and its version follows the node.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from anygarden.machine.agent_root_lock import AgentRootInUseError, AgentRootLock
from anygarden.machine.config import (
    MachineConfig,
    MachinePaths,
    load_token,
    save_connection,
)
from anygarden.machine.daemon import MachineDaemon, _base_url_from_machine_url
from anygarden.machine.link_code import MACHINE_ID_PATTERN, decode_link_code
from anygarden.machine.safefs import secure_chmod

log = structlog.get_logger("machine_links")

LINKS_DIRNAME = "remote-machines"


def _server_base_url(ws_url: str) -> str:
    base = _base_url_from_machine_url(ws_url)
    if base.startswith("wss://"):
        return "https://" + base[len("wss://") :]
    if base.startswith("ws://"):
        return "http://" + base[len("ws://") :]
    return base


class MachineLinkError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class _Link:
    machine_id: str
    paths: MachinePaths
    config: MachineConfig
    lock: AgentRootLock
    daemon: Any = None
    task: asyncio.Task | None = None
    error: str | None = None


class MachineLinkManager:
    def __init__(
        self,
        data_dir: Path,
        *,
        local_machine_id: str | None,
        agent_environment: dict[str, str] | None = None,
        daemon_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.root = data_dir / LINKS_DIRNAME
        self.local_machine_id = local_machine_id
        self.agent_environment = agent_environment
        self.daemon_factory = daemon_factory or MachineDaemon
        self._links: dict[str, _Link] = {}
        self._mutex = asyncio.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start_all(self) -> None:
        """Resume every saved link. A broken one is reported, never fatal."""
        if not self.root.is_dir():
            return
        async with self._mutex:
            for entry in sorted(self.root.iterdir()):
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if not MACHINE_ID_PATTERN.fullmatch(entry.name) or entry.name in self._links:
                    continue
                paths = MachinePaths.for_data_dir(entry)
                try:
                    config = MachineConfig.load(paths.config)
                except Exception as exc:  # noqa: BLE001 — shown, not fatal
                    config = MachineConfig(machine_id=entry.name, name=entry.name)
                    link = self._new_link(entry.name, paths, config)
                    link.error = f"Saved connection could not be read: {exc}"
                    continue
                self._start(self._new_link(entry.name, paths, config))

    async def close(self) -> None:
        async with self._mutex:
            for link in list(self._links.values()):
                await self._stop(link)
            self._links.clear()

    # ── admin operations ───────────────────────────────────────────────

    async def add(self, code: str) -> dict:
        try:
            parsed = decode_link_code(code)
        except ValueError as exc:
            raise MachineLinkError("MACHINE_LINK_CODE_INVALID", str(exc)) from exc
        if parsed.machine_id == self.local_machine_id:
            raise MachineLinkError(
                "MACHINE_LINK_SELF",
                "This code is for this node's own machine.",
                409,
            )
        async with self._mutex:
            if parsed.machine_id in self._links:
                raise MachineLinkError(
                    "MACHINE_LINK_EXISTS",
                    "This node already serves that machine.",
                    409,
                )
            paths = MachinePaths.for_data_dir(self.root / parsed.machine_id)
            config = MachineConfig(
                machine_id=parsed.machine_id, name=parsed.name, server_url=parsed.ws_url
            )
            self.root.mkdir(parents=True, exist_ok=True)
            secure_chmod(self.root, 0o700)
            save_connection(config, parsed.token, paths)
            link = self._new_link(parsed.machine_id, paths, config)
            self._start(link)
            log.info("machine_link.added", machine_id=parsed.machine_id, server=parsed.server)
            return self._describe(link)

    async def remove(self, machine_id: str) -> None:
        async with self._mutex:
            link = self._links.get(machine_id)
            if link is None:
                raise MachineLinkError("MACHINE_LINK_NOT_FOUND", "No such connection.", 404)
            if not link.lock.held:
                # Another daemon runs from this directory; deleting it would
                # pull its saved connection and agent state from under it.
                raise MachineLinkError(
                    "MACHINE_LINK_IN_USE",
                    "Another machine daemon on this computer uses this connection. "
                    "Stop it first.",
                    409,
                )
            del self._links[machine_id]
            await self._stop(link)
            if link.paths.data_dir is not None:
                shutil.rmtree(link.paths.data_dir, ignore_errors=True)
            log.info("machine_link.removed", machine_id=machine_id)

    def list(self) -> list[dict]:
        return [self._describe(link) for link in self._links.values()]

    # ── internals ──────────────────────────────────────────────────────

    def _new_link(self, machine_id: str, paths: MachinePaths, config: MachineConfig) -> _Link:
        link = _Link(
            machine_id=machine_id,
            paths=paths,
            config=config,
            lock=AgentRootLock(paths.agents_root),
        )
        self._links[machine_id] = link
        return link

    def _start(self, link: _Link) -> None:
        try:
            link.lock.acquire()
        except AgentRootInUseError:
            link.error = (
                "Another machine daemon on this computer already uses this "
                "connection's directory. Stop it, then restart the node."
            )
            return
        token = load_token(link.paths.token)
        link.daemon = self.daemon_factory(
            server_url=link.config.server_url,
            machine_id=link.machine_id,
            machine_token=token,
            labels=link.config.labels,
            token_path=link.paths.token,
            agent_dirs_root=link.paths.agents_root,
            workspace_registry_path=link.paths.workspace_registry,
            workspace_signing_key_path=link.paths.signing_key,
            agent_environment=self.agent_environment,
            allow_self_update=False,
        )
        link.task = asyncio.create_task(
            link.daemon.run(), name=f"machine-link-{link.machine_id}"
        )
        link.task.add_done_callback(lambda task, link=link: self._finished(link, task))

    def _finished(self, link: _Link, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            link.error = f"The connection stopped: {exc}"
            log.error("machine_link.crashed", machine_id=link.machine_id, error=str(exc))

    async def _stop(self, link: _Link) -> None:
        if link.task is not None and not link.task.done():
            link.task.cancel()
            # The daemon drains (stops) its own agents when cancelled.
            await asyncio.gather(link.task, return_exceptions=True)
        link.lock.release()

    def _describe(self, link: _Link) -> dict:
        daemon = link.daemon
        if link.error is not None:
            status = "error"
        elif daemon is None:
            status = "stopped"
        else:
            status = daemon.connection_state
        running = 0
        if daemon is not None:
            try:
                running = len(daemon._spawner.list_running())
            except Exception:  # noqa: BLE001 — status only
                running = 0
        return {
            "machine_id": link.machine_id,
            "name": link.config.name or link.machine_id,
            "server": _server_base_url(link.config.server_url),
            "status": status,
            "error": link.error or (daemon.last_error if daemon is not None else None),
            "running_agents": running,
        }
