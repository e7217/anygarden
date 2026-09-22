"""Normal room turns on the same receipt/session/process manager as federation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

from anygarden_agent.integrations._turn_timeout import resolve_turn_timeout
from anygarden_agent.integrations.codex_cli import (
    CodexCliAdapter,
    register_room_adapter,
)
from anygarden_agent.integrations.engine_session_store import load_sessions
from anygarden_agent.runtime.execution.contracts import SessionScope
from anygarden_agent.runtime.execution.launch import load_execution_launch
from anygarden_agent.runtime.execution.manager import LocalExecutionManager
from anygarden_agent.runtime.execution.room import (
    RoomCodexRuntime,
    RoomInvocation,
    RoomPiRuntime,
    staged_environment,
)
from anygarden_agent.runtime.handler_wrapper import (
    EngineCancelledError,
    EngineError,
    EngineTimeoutError,
    EngineTurn,
)


class RoomExecutionAdapter(CodexCliAdapter):
    """Reuse room prompt/context composition; execute only through the manager."""

    def __init__(self, *, engine="codex-cli", **kwargs):
        super().__init__(**kwargs)
        self._engine = engine
        self._root = Path.cwd().resolve()
        self._turn_metadata = ContextVar("room_execution_metadata", default=None)
        self._usage = ContextVar("room_execution_usage", default=None)
        self._manager = None
        self._turn_timeout = resolve_turn_timeout(
            "pi" if engine == "pi-cli" else "codex"
        )

    async def start(self):
        self._codex_path = shutil.which("pi" if self._engine == "pi-cli" else "codex")
        if self._codex_path is None:
            raise ValueError(f"{self._engine} executable is not installed")
        self._launch = self._client.execution_launch
        if self._launch is None:
            self._launch = load_execution_launch(
                engine=self._engine,
                provider=None,
                model=self._model,
                generation=getattr(self._client, "_generation", None) or 0,
            )
        self._environment = staged_environment()
        # Pin the effective existing Codex home, including host OAuth fallback.
        if self._engine == "codex-cli":
            home = self._environment.get("CODEX_HOME") or str(Path.home() / ".codex")
            self._environment["CODEX_HOME"] = home
        runtime_cls = RoomPiRuntime if self._engine == "pi-cli" else RoomCodexRuntime
        runtime = runtime_cls(Path(self._codex_path).absolute())
        self._runtime_version = runtime.capabilities().engine_version
        self._manager = LocalExecutionManager(
            self._root / ".anygarden-execution" / self._engine,
            runtime,
            authorize=self._authorized,
        )
        self._room_thread_ids = (
            load_sessions(self._root) if self._engine == "codex-cli" else {}
        )

    def _authorized(self, scope):
        generation = getattr(self._client, "_generation", None)
        return (
            scope.engine == self._engine
            and scope.agent_id == self._identity()
            and (generation is None or generation == self._launch.generation)
        )

    def _identity(self):
        return (
            getattr(self._client, "_agent_id", None)
            or hashlib.sha256(str(self._root).encode()).hexdigest()
        )

    def _context_scope(self, msg):
        return (msg.get("room_id", "_default"), msg.get("root_message_id"))

    async def on_message(self, msg):
        token = self._turn_metadata.set(msg)
        self._usage.set(None)
        try:
            return await super().on_message(msg)
        finally:
            self._turn_metadata.reset(token)

    def _take_last_usage(self):
        usage = self._usage.get()
        self._usage.set(None)
        return usage

    async def _call_codex(self, prompt, room_id):
        # The inherited name is the prompt adapter seam, not an engine choice.
        msg = self._turn_metadata.get() or {}
        workspace = self._root / "workspace"
        if not workspace.is_dir():
            workspace = self._root
        authority = hashlib.sha256(self._client._server_url.encode()).hexdigest()
        tier = self._permission_level or "standard"
        policy_epoch = int.from_bytes(hashlib.sha256(tier.encode()).digest()[:4], "big")
        scope = SessionScope(
            execution_node_id=authority,
            agent_id=self._identity(),
            authority_node_id=authority,
            channel_id=room_id,
            thread_root_id=msg.get("root_message_id"),
            workspace_binding_id=str(workspace),
            workspace_epoch=0,
            policy_epoch=policy_epoch,
            engine=self._engine,
            engine_version=self._runtime_version,
        )
        runtime_home = self._root
        invocation = RoomInvocation(
            execution_id=str(uuid4()),
            scope=scope,
            prompt=prompt,
            workspace=workspace,
            runtime_home=runtime_home,
            reasoning_effort=self._reasoning_effort,
            permission_level=tier,
            timeout_seconds=self._turn_timeout,
            environment=self._environment,
        )
        invocation = self._launch.bind(invocation)
        # Legacy mappings had no endpoint/thread scope. Import once only for
        # unchanged default-provider root-room sessions; never for direct mode.
        legacy = self._room_thread_ids.get(room_id)
        if legacy and not scope.thread_root_id:
            self._manager.import_legacy_session(
                invocation.scope,
                legacy if self._legacy_compatible(legacy, invocation) else None,
                room_id,
            )
        await self._manager.start(invocation)
        try:
            async for _ in self._manager.events(invocation.execution_id):
                pass
            receipt = await self._manager.reconcile(invocation.execution_id)
        except asyncio.CancelledError:

            async def finish_cancel():
                await self._manager.cancel(invocation.execution_id)
                async for _ in self._manager.events(invocation.execution_id):
                    pass
                return await self._manager.reconcile(invocation.execution_id)

            cleanup = asyncio.create_task(finish_cancel())
            try:
                receipt = await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                receipt = await cleanup
            raise EngineCancelledError(
                self._telemetry(receipt, invocation.model)
            ) from None
        turn = self._telemetry(receipt, invocation.model)
        self._usage.set(
            {
                "model": turn.model,
                "input_tokens": turn.input_tokens,
                "output_tokens": turn.output_tokens,
                "cost_usd": turn.cost_usd,
            }
        )
        if receipt.outcome == "succeeded":
            return receipt.text
        if receipt.outcome == "cancelled":
            raise EngineCancelledError(turn)
        error_cls = (
            EngineTimeoutError if receipt.reason == "TIMEOUT_STOPPED" else EngineError
        )
        # Receipt codes only: no provider stderr or credentials in logs/notices.
        raise error_cls(receipt.reason or "runtime_error", turn=turn)

    def _legacy_compatible(self, handle, invocation):
        from .room_session_upgrade import legacy_codex_session_matches

        if invocation.endpoint is not None:
            return False
        return legacy_codex_session_matches(
            Path(self._environment["CODEX_HOME"]),
            handle,
            workspace=invocation.workspace,
            model=invocation.model or self._model,
            provider=invocation.provider,
        )

    @staticmethod
    def _telemetry(receipt, model):
        usage = receipt.usage or {}
        return EngineTurn(
            None,
            model=model,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    async def stop(self):
        if self._manager is not None:
            await self._manager.close()
            self._manager = None


async def integrate_with_room_execution(
    client,
    *,
    engine,
    model=None,
    system_prompt="",
    reasoning_effort=None,
    permission_level=None,
):
    adapter = RoomExecutionAdapter(
        engine=engine,
        model=model,
        system_prompt=system_prompt,
        reasoning_effort=reasoning_effort,
        permission_level=permission_level
        or os.environ.get("ANYGARDEN_AGENT_PERMISSION_LEVEL"),
    )
    adapter._client = client
    await adapter.start()
    register_room_adapter(client, adapter, engine, adapter._turn_timeout)
    client._execution_adapter = adapter
    client.execution_launch_ready = True
    return adapter
