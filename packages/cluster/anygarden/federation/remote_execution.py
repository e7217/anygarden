"""Server-only descriptors and agent transport adapter; never launches a local CLI."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import select

from .delegation_models import ExecutorBinding
from .executor import canonical


@dataclass(frozen=True)
class RemoteScope:
    execution_node_id: str
    agent_id: str
    authority_node_id: str
    channel_id: str
    thread_root_id: str | None
    workspace_binding_id: str
    workspace_epoch: int
    policy_epoch: int
    engine: str = "codex-cli"
    engine_version: str = "0.154.0"

    @property
    def key(self) -> str:
        return hashlib.sha256(canonical(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class PreparedExecution:
    execution_id: str
    scope: RemoteScope
    fingerprint: str
    generation: int

    def validate(self):
        for value in (
            self.execution_id,
            self.scope.execution_node_id,
            self.scope.agent_id,
            self.scope.authority_node_id,
            self.scope.channel_id,
        ):
            UUID(value)
        if self.scope.thread_root_id is not None:
            UUID(self.scope.thread_root_id)
        if (
            len(self.fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in self.fingerprint)
            or not self.scope.workspace_binding_id
            or len(self.scope.workspace_binding_id) > 256
            or self.scope.engine not in {"codex-cli", "pi-cli"}
            or not self.scope.engine_version
            or any(
                type(v) is not int or v < 0
                for v in (
                    self.generation,
                    self.scope.workspace_epoch,
                    self.scope.policy_epoch,
                )
            )
        ):
            raise ValueError("Invalid prepared execution")

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != {
            "execution_id",
            "scope",
            "fingerprint",
            "generation",
        }:
            raise ValueError("Invalid prepared execution response")
        value = cls(
            data["execution_id"],
            RemoteScope(**data["scope"]),
            data["fingerprint"],
            data["generation"],
        )
        value.validate()
        return value


@dataclass(frozen=True)
class RemoteReceipt:
    execution_id: str
    state: str
    process_state: str
    outcome: str | None = None
    reason: str | None = None
    text: str | None = None
    usage: dict | None = None
    error_code: str | None = None

    @classmethod
    def parse(cls, data, execution_id):
        value = cls(**data)
        if (
            value.execution_id != execution_id
            or value.process_state
            not in {"not_started", "running", "finished", "stopped", "unknown"}
            or value.outcome
            not in {None, "succeeded", "failed", "cancelled", "unknown"}
            or not isinstance(value.state, str)
            or (
                value.text is not None
                and (
                    not isinstance(value.text, str)
                    or len(value.text.encode()) > 1_048_576
                )
            )
        ):
            raise ValueError("Invalid execution receipt")
        return value


class RemoteExecutionManager:
    def __init__(self, sessions, transport):
        self.sessions, self.transport = sessions, transport
        self._prepared: dict[str, PreparedExecution] = {}

    async def prepare(self, agent_id, *, generation, **payload):
        data = await self.transport.request(
            agent_id, "prepare", payload, expected_generation=generation
        )
        prepared = PreparedExecution.parse(data)
        if (
            prepared.execution_id != payload["execution_id"]
            or prepared.generation != generation
            or prepared.scope.agent_id != agent_id
            or any(
                getattr(prepared.scope, k) != payload[k]
                for k in ("execution_node_id", "authority_node_id", "channel_id")
            )
        ):
            raise ValueError("Prepared execution scope mismatch")
        self._prepared[prepared.execution_id] = prepared
        return prepared

    async def descriptor(self, execution_id):
        # SQL binding is authoritative after process restart; no prompt/keys needed
        # to ask the placed agent to reconcile its durable local receipt.
        async with self.sessions() as db:
            row = await db.scalar(
                select(ExecutorBinding).where(
                    ExecutorBinding.execution_id == execution_id
                )
            )
            if row is not None:
                value = PreparedExecution(
                    execution_id,
                    RemoteScope(**row.scope),
                    row.invocation_fingerprint,
                    row.generation,
                )
                value.validate()
                self._prepared.pop(execution_id, None)
                return value
        if execution_id not in self._prepared:
            raise KeyError(execution_id)
        return self._prepared[execution_id]

    async def _request(self, value, action):
        try:
            data = await self.transport.request(
                value.scope.agent_id,
                action,
                {"execution_id": value.execution_id, "fingerprint": value.fingerprint},
                expected_generation=value.generation if action == "start" else None,
            )
        except Exception as exc:
            if getattr(exc, "code", None) == "EXECUTION_UNKNOWN":
                raise KeyError(value.execution_id) from exc
            raise
        return RemoteReceipt.parse(data, value.execution_id)

    async def start(self, invocation):
        invocation.validate()
        return await self._request(invocation, "start")

    async def reconcile(self, execution_id):
        return await self._request(await self.descriptor(execution_id), "reconcile")

    async def cancel(self, execution_id):
        return await self._request(await self.descriptor(execution_id), "cancel")

    async def retire_unstarted(self, binding):
        """Recover an ACK-lost prepare without recreating or launching it.

        The server's synthetic no-start tombstone cannot carry the agent's
        actual prepare fingerprint. Read only the matching owned record before
        cancellation; a missing record is already retired.
        """
        payload = {
            "execution_id": binding.execution_id,
            "execution_node_id": binding.scope["execution_node_id"],
            "authority_node_id": binding.authority_node_id,
            "channel_id": binding.channel_id,
        }
        try:
            data = await self.transport.request(binding.agent_id, "describe", payload)
        except Exception as exc:
            if getattr(exc, "code", None) == "EXECUTION_UNKNOWN":
                return
            raise
        value = PreparedExecution.parse(data)
        if (
            value.execution_id != binding.execution_id
            or value.scope.agent_id != binding.agent_id
            or any(
                getattr(value.scope, key) != payload[key]
                for key in ("execution_node_id", "authority_node_id", "channel_id")
            )
        ):
            raise ValueError("Prepared execution scope mismatch")
        await self._request(value, "cancel")

    async def revoke(self, scope):
        async with self.sessions() as db:
            rows = list(
                await db.scalars(
                    select(ExecutorBinding).where(
                        ExecutorBinding.agent_id == scope.agent_id
                    )
                )
            )
        for row in rows:
            if RemoteScope(**row.scope).key == scope.key:
                await self._request(await self.descriptor(row.execution_id), "revoke")

    async def close(self):
        # Product shutdown owns only these remote executions, never the ordinary
        # room runtime. Requests are bounded by the transport's timeout.
        async with self.sessions() as db:
            ids = list(
                await db.scalars(
                    select(ExecutorBinding.execution_id).where(
                        ExecutorBinding.authority_state.in_(
                            ["accepted", "running", "cancel_requested"]
                        )
                    )
                )
            )
        await asyncio.gather(
            *(self.cancel(value) for value in ids), return_exceptions=True
        )
