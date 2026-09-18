"""Committed delivery and local execution bridge; no transport/app ownership.

One bridge is owned by the node's single LocalExecutionManager. The supplied
policy callback must synchronously check current generation, lease, workspace
and grant epochs; it is called again by the runtime before writing input.
Transport callbacks must authenticate the peer and validate the wire schemas.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text

from anygarden.shared_channels.schemas import validate

from .delegation import FAILURE_CODES, TASK_STATUS, DelegationError
from .delegation_models import DelegationOutbox, ExecutorBinding

if TYPE_CHECKING:
    from anygarden_agent.runtime.execution import Invocation, SessionScope
    from anygarden_agent.runtime.execution.contracts import Runtime


@dataclass(frozen=True)
class ExecutionFence:
    scope: dict
    generation: int
    lease_token: str
    grant_epoch: int


@dataclass(frozen=True)
class AuthoritySnapshot:
    """Returned only by an authenticated, freshly authorized authority lookup."""

    authority_node_id: str
    channel_id: str
    delegation_id: str
    execution_id: str | None
    revision: int
    state: str


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class ExecutorBridge:
    def __init__(
        self,
        sessions,
        directory: Path,
        runtime: Runtime,
        *,
        node_id: str,
        permits: Callable[[ExecutionFence], bool],
        reauthorize: Callable,
    ):
        # Lazy import: the cluster/core distribution need not install agent extras.
        from anygarden_agent.runtime.execution import LocalExecutionManager

        self._closed = False
        self.sessions = sessions
        self.node_id = node_id
        self.permits = permits
        self.reauthorize = reauthorize
        self._fences: dict[str, ExecutionFence] = {}
        self._disabled: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self.manager = LocalExecutionManager(
            directory, runtime, authorize=self._authorized_scope
        )

    def _authorized_scope(self, scope: SessionScope) -> bool:
        fence = self._fences.get(scope.key)
        return (
            fence is not None
            and scope.key not in self._disabled
            and self.permits(fence)
        )

    @staticmethod
    def _fence(binding):
        return ExecutionFence(
            binding.scope, binding.generation, binding.lease_token, binding.grant_epoch
        )

    def _install(self, binding):
        from anygarden_agent.runtime.execution import SessionScope

        scope = SessionScope(**binding.scope)
        fence = self._fence(binding)
        old = self._fences.get(scope.key)
        if old is not None and old != fence:
            raise DelegationError("SCOPE_EPOCH_CONFLICT")
        self._fences[scope.key] = fence
        return scope

    @asynccontextmanager
    async def _transaction(self):
        async with self.sessions() as db:
            # SQLite SELECT FOR UPDATE is ignored; reserve the writer before reads.
            if db.bind.dialect.name == "sqlite":
                await db.execute(text("BEGIN IMMEDIATE"))
            else:
                await db.begin()
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _binding(self, db, delegation_id, *, authorize=True):
        binding = await db.scalar(
            select(ExecutorBinding)
            .where(ExecutorBinding.delegation_id == delegation_id)
            .with_for_update()
        )
        if binding is None:
            raise DelegationError("BINDING_MISSING")
        if authorize:
            await self.reauthorize(db, binding)
            if not self.permits(self._fence(binding)):
                raise DelegationError("LOCAL_FENCE_DENIED")
        self._install(binding)
        return binding

    async def _pending(self, db, binding):
        return await db.scalar(
            select(DelegationOutbox).where(
                DelegationOutbox.delegation_id == binding.delegation_id,
                DelegationOutbox.state == "pending",
            )
        )

    async def _enqueue(self, db, binding, kind, **extra):
        pending = await self._pending(db, binding)
        if pending is not None:
            return pending.id
        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"delegation:{binding.delegation_id}:{binding.revision}:{kind}",
            )
        )
        command = {
            "protocol_version": 1,
            "request_id": request_id,
            "sender_node_id": self.node_id,
            "authority_node_id": binding.authority_node_id,
            "channel_id": binding.channel_id,
            "grant_epoch": binding.grant_epoch,
            "actor": {
                "node_id": self.node_id,
                "kind": "agent",
                "principal_id": binding.agent_id,
            },
            "kind": kind,
            "payload": dict(
                delegation_id=binding.delegation_id,
                expected_revision=binding.revision,
                execution_id=binding.execution_id,
                **extra,
            ),
        }
        validate("command", command)
        if len(canonical(command).encode("utf-8")) > 55000:
            # Reserve space for #591's receipt/event framing within 60 KiB.
            if kind == "task.result":
                return await self._enqueue(db, binding, "task.unknown")
            raise DelegationError("COMMAND_TOO_LARGE")
        db.add(
            DelegationOutbox(
                id=request_id,
                delegation_id=binding.delegation_id,
                command=command,
                canonical_body=canonical(command),
                state="pending",
            )
        )
        await db.flush()
        return request_id

    async def prepare(
        self,
        delegation_id: str,
        invocation: Invocation,
        *,
        generation: int,
        lease_token: str,
        grant_epoch: int,
        requested_revision: int = 1,
    ) -> str:
        """Persist intent before asking authority to accept. Does not execute."""
        invocation.validate()
        if (
            invocation.scope.execution_node_id != self.node_id
            or not lease_token
            or generation < 0
            or grant_epoch < 1
        ):
            raise DelegationError("INVALID_BINDING")
        async with self._transaction() as db:
            binding = await db.get(ExecutorBinding, delegation_id)
            if binding is None:
                binding = ExecutorBinding(
                    delegation_id=delegation_id,
                    authority_node_id=invocation.scope.authority_node_id,
                    channel_id=invocation.scope.channel_id,
                    agent_id=invocation.scope.agent_id,
                    generation=generation,
                    lease_token=lease_token,
                    grant_epoch=grant_epoch,
                    execution_id=invocation.execution_id,
                    invocation_fingerprint=invocation.fingerprint,
                    scope=asdict(invocation.scope),
                    revision=requested_revision,
                    authority_state="requested",
                    # D-3: a suppressed executor records a declined intent —
                    # binding-anchored outbox stays intact, no launch ever
                    # happens from this state, and recovery can accept later.
                    local_state=(
                        "declined" if await self._self_suppressed(invocation) else "prepared"
                    ),
                )
                # Auth BEFORE the first insert as well as BEFORE duplicate lookup result.
                await self.reauthorize(db, binding)
                if (
                    binding.local_state == "prepared"
                    and not self.permits(self._fence(binding))
                ):
                    raise DelegationError("LOCAL_FENCE_DENIED")
                self._install(binding)
                db.add(binding)
                await db.flush()
                if binding.local_state == "declined":
                    return await self._enqueue_decline(db, binding)
                return await self._enqueue(db, binding, "task.accept")
            binding = await self._binding(db, delegation_id)
            if (
                binding.invocation_fingerprint != invocation.fingerprint
                or binding.generation != generation
                or binding.lease_token != lease_token
                or binding.grant_epoch != grant_epoch
            ):
                raise DelegationError("BINDING_CONFLICT")
            if binding.local_state == "declined":
                # Still suppressed -> the same decline (idempotent request id);
                # recovered -> flip to prepared and accept honestly.
                if await self._self_suppressed(invocation):
                    return await self._enqueue_decline(db, binding)
                binding.local_state = "prepared"
                if not self.permits(self._fence(binding)):
                    raise DelegationError("LOCAL_FENCE_DENIED")
                await db.flush()
                return await self._enqueue(db, binding, "task.accept")
            row = await db.scalar(
                select(DelegationOutbox).where(
                    DelegationOutbox.delegation_id == delegation_id,
                    DelegationOutbox.command["kind"].as_string() == "task.accept",
                )
            )
            if row is None:
                raise DelegationError("ACCEPT_MISSING")
            return row.id

    @staticmethod
    def _validate_receipt(command, receipt):
        required = {
            "protocol_version",
            "request_id",
            "authority_node_id",
            "channel_id",
            "event_id",
            "seq",
            "revision",
            "state",
            "process_state",
            "task_status",
        }
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise DelegationError("INVALID_RECEIPT")
        try:
            UUID(receipt["event_id"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise DelegationError("INVALID_RECEIPT") from exc
        if any(
            type(receipt[k]) is not int or not 1 <= receipt[k] <= 9007199254740991
            for k in ("seq", "revision")
        ):
            raise DelegationError("INVALID_RECEIPT")
        expected_state = {
            "task.accept": "accepted",
            "task.reject": "rejected",
            "task.started": "running",
            "task.result": "completed"
            if command["payload"].get("outcome") == "succeeded"
            else "failed",
            "task.cancelled": "cancelled",
            "task.unknown": "unknown",
        }[command["kind"]]
        expected_process = {
            "accepted": "unknown",
            "rejected": "not_started",
            "running": "running",
            "completed": "finished",
            "failed": "finished",
            "cancelled": command["payload"].get("process_state"),
            "unknown": "unknown",
        }[expected_state]
        if (
            any(
                receipt.get(k) != command[k]
                for k in (
                    "protocol_version",
                    "request_id",
                    "authority_node_id",
                    "channel_id",
                )
            )
            or receipt.get("revision") != command["payload"]["expected_revision"] + 1
            or receipt.get("state") != expected_state
            or receipt.get("process_state") != expected_process
            or receipt.get("task_status") != TASK_STATUS[expected_state]
            or not receipt.get("event_id")
            or not isinstance(receipt.get("seq"), int)
            or receipt["seq"] < 1
        ):
            raise DelegationError("INVALID_RECEIPT")

    async def deliver(self, outbox_id: str, send: Callable) -> dict:
        """Every retry sends the identical persisted body, after fresh authorization.

        send returns only after authority commit. Exceptions (including ACK loss)
        leave intent pending. Its input is a detached copy, never a mutable DB row.
        """
        async with self._transaction() as db:
            row = await db.get(DelegationOutbox, outbox_id)
            if row is None:
                raise DelegationError("OUTBOX_MISSING")
            await self._binding(db, row.delegation_id)
            if row.state == "delivered":
                return row.receipt
            if row.state != "pending":
                raise DelegationError("OUTBOX_SUPERSEDED")
            command = json.loads(row.canonical_body)
        receipt = await send(command)
        self._validate_receipt(command, receipt)
        async with self._transaction() as db:
            row = await db.get(DelegationOutbox, outbox_id)
            binding = await self._binding(db, row.delegation_id)
            if row.state != "pending":
                if row.state == "delivered" and row.receipt == receipt:
                    return receipt
                raise DelegationError("OUTBOX_SUPERSEDED")
            row.receipt, row.state = receipt, "delivered"
            # A delayed accept ACK cannot undo an observed cancellation.
            if receipt["revision"] > binding.revision:
                binding.revision, binding.authority_state = (
                    receipt["revision"],
                    receipt["state"],
                )
                if receipt["state"] == "accepted" and binding.local_state == "prepared":
                    binding.local_state = "accepted"
        return receipt

    @staticmethod
    def _check_snapshot(binding, snapshot):
        if (
            snapshot.authority_node_id != binding.authority_node_id
            or snapshot.channel_id != binding.channel_id
            or snapshot.delegation_id != binding.delegation_id
            or snapshot.execution_id not in {None, binding.execution_id}
            or snapshot.revision < binding.revision
        ):
            raise DelegationError("STALE_AUTHORITY")
        if snapshot.execution_id is None and snapshot.state not in {
            "requested",
            "cancel_requested",
            "cancelled",
        }:
            raise DelegationError("EXECUTION_MISMATCH")

    async def launch(
        self, delegation_id: str, invocation: Invocation, confirm: Callable
    ):
        """Confirm committed acceptance, then claim launch once before manager.start.

        A crash after launch_intent is conservative: recover/observe, never start
        again when the runtime receipt is missing. No SQL transaction spans I/O.
        """
        async with self._locks.setdefault(delegation_id, asyncio.Lock()):
            async with self._transaction() as db:
                binding = await self._binding(db, delegation_id)
                if invocation.fingerprint != binding.invocation_fingerprint:
                    raise DelegationError("BINDING_CONFLICT")
                if binding.local_state != "accepted":
                    raise DelegationError("LAUNCH_ALREADY_CLAIMED")
            snapshot = await confirm(delegation_id)
            async with self._transaction() as db:
                binding = await self._binding(db, delegation_id)
                self._check_snapshot(binding, snapshot)
                if (
                    snapshot.state != "accepted"
                    or binding.authority_state != "accepted"
                    or binding.local_state != "accepted"
                ):
                    raise DelegationError("ACCEPT_REQUIRED")
                binding.revision = snapshot.revision
                binding.local_state = "launch_intent"
            # manager rechecks the identical fence in its queued task and runtime.
            return await self.manager.start(invocation)

    async def observe(self, delegation_id: str) -> str | None:
        """Translate durable runtime receipts to committed outbox intent only."""
        async with (
            self._locks.setdefault(delegation_id, asyncio.Lock()),
            self._transaction() as db,
        ):
            binding = await self._binding(db, delegation_id)
            if binding.authority_state not in {
                "accepted",
                "running",
                "cancel_requested",
            }:
                return None
            if binding.local_state in {"prepared", "accepted"}:
                return None
            try:
                receipt = await self.manager.reconcile(binding.execution_id)
            except KeyError:
                # Could have crashed before or after spawn. Never fabricate stop.
                binding.local_state = "unknown"
                return await self._enqueue(db, binding, "task.unknown")
            if receipt.outcome == "unknown":
                binding.local_state = "unknown"
                return await self._enqueue(db, binding, "task.unknown")
            if binding.authority_state == "cancel_requested":
                if receipt.outcome is not None and receipt.process_state in {
                    "stopped",
                    "not_started",
                    "finished",
                }:
                    return await self._enqueue(
                        db,
                        binding,
                        "task.cancelled",
                        process_state="not_started"
                        if receipt.process_state == "not_started"
                        else "stopped",
                    )
                return None
            if (
                receipt.outcome == "succeeded"
                and receipt.text
                and len(receipt.text) <= 16384
                and len(receipt.text.encode("utf-8")) <= 60000
            ):
                return await self._enqueue(
                    db,
                    binding,
                    "task.result",
                    outcome="succeeded",
                    text=receipt.text,
                )
            if receipt.outcome == "failed" and receipt.error_code in FAILURE_CODES:
                return await self._enqueue(
                    db,
                    binding,
                    "task.result",
                    outcome="failed",
                    error_code=receipt.error_code,
                )
            if receipt.outcome is not None:
                # Empty/oversized success, spontaneous cancellation: no fabricated result.
                return await self._enqueue(db, binding, "task.unknown")
            if (
                receipt.process_state == "running"
                and binding.authority_state == "accepted"
            ):
                return await self._enqueue(db, binding, "task.started")
            return None

    async def cancel(
        self, delegation_id: str, snapshot: AuthoritySnapshot
    ) -> str | None:
        """Consume authenticated cancellation; stop is reported only with proof."""
        async with self._locks.setdefault(delegation_id, asyncio.Lock()):
            async with self._transaction() as db:
                binding = await self._binding(db, delegation_id)
                self._check_snapshot(binding, snapshot)
                if snapshot.state != "cancel_requested":
                    raise DelegationError("CANCEL_REQUIRED")
                if (
                    binding.revision == snapshot.revision
                    and binding.authority_state == "cancel_requested"
                ):
                    pending = await self._pending(db, binding)
                    if pending is not None:
                        return pending.id
                binding.revision, binding.authority_state = (
                    snapshot.revision,
                    snapshot.state,
                )
                pending = await self._pending(db, binding)
                if pending:
                    pending.state = "superseded"
                unstarted = binding.local_state in {"prepared", "accepted"}
                binding.local_state = "cancel_requested"
                execution_id = binding.execution_id
                if unstarted:
                    return await self._enqueue(
                        db, binding, "task.cancelled", process_state="not_started"
                    )
            try:
                await self.manager.cancel(execution_id)
            except KeyError:
                # A launch_intent without runtime receipt remains ambiguous.
                pass
        return await self.observe(delegation_id)

    async def revoke(self, scope: SessionScope):
        """Trusted policy owner calls this after denying the fence; bypasses read ACL."""
        self._disabled.add(scope.key)
        await self.manager.revoke(scope)

    async def close(self):
        if not self._closed:
            self._closed = True
            await self.manager.close()

    async def _self_suppressed(self, invocation) -> bool:
        """D-3 voluntary suppression: decline work this node cannot run.

        The authority's selection already avoids known-blocked agents, but
        availability is freshest here: quota windows, budget pauses and
        hard-stop ceilings are checked at accept time so the delegation
        fails honestly (task.reject UNAVAILABLE) instead of stalling.
        """
        from anygarden.agent_availability import routing_blocked
        from anygarden.budgets.ledger import evaluate_invocation_block
        from anygarden.db.models import Agent as AgentRow

        async with self.sessions() as db:
            agent = await db.get(AgentRow, invocation.scope.agent_id)
            if agent is not None and (
                routing_blocked(agent, now=datetime.now(UTC))
                or agent.pause_reason == "budget"
            ):
                return True
        block = await evaluate_invocation_block(
            self.sessions,
            agent_id=invocation.scope.agent_id,
            room_id=invocation.scope.channel_id,
        )
        return block is not None

    async def _enqueue_decline(self, db, binding) -> str:
        """Emit task.reject UNAVAILABLE for a suppressed executor (D-3).

        Binding-anchored like every outbox command; the uuid5 request id
        makes repeated prepares idempotent. The reject payload deliberately
        carries no execution_id (closed wire schema).
        """
        pending = await db.scalar(
            select(DelegationOutbox.id).where(
                DelegationOutbox.delegation_id == binding.delegation_id,
                DelegationOutbox.command["kind"].as_string() == "task.reject",
                DelegationOutbox.state == "pending",
            )
        )
        if pending is not None:
            return pending
        request_id = str(
            uuid5(
                NAMESPACE_URL,
                f"delegation:{binding.delegation_id}:{binding.revision}:task.reject:UNAVAILABLE",
            )
        )
        command = {
            "protocol_version": 1,
            "request_id": request_id,
            "sender_node_id": self.node_id,
            "authority_node_id": binding.authority_node_id,
            "channel_id": binding.channel_id,
            "grant_epoch": binding.grant_epoch,
            "actor": {
                "node_id": self.node_id,
                "kind": "agent",
                "principal_id": binding.agent_id,
            },
            "kind": "task.reject",
            "payload": {
                "delegation_id": binding.delegation_id,
                "expected_revision": binding.revision,
                "reason": "UNAVAILABLE",
            },
        }
        validate("command", command)
        db.add(
            DelegationOutbox(
                id=request_id,
                delegation_id=binding.delegation_id,
                command=command,
                canonical_body=canonical(command),
                state="pending",
            )
        )
        await db.flush()
        return request_id
