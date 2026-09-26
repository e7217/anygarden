"""Lifecycle-owned shared-channel sync and deployed-agent execution coordinator."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from uuid import NAMESPACE_URL, uuid5

import structlog
from sqlalchemy import select

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent
from anygarden.shared_channels.models import ChannelStream, ChannelSubmission
from anygarden.shared_channels.sync import pull, retry_submission

from .delegation import DelegationError
from .delegation_models import (
    Delegation,
    DelegationMirror,
    DelegationOutbox,
    ExecutorBinding,
)
from .execution_context import (
    require_local_executor,
    resolve_execution_context,
    send_executor_command,
)
from .executor import AuthoritySnapshot, ExecutorBridge, canonical
from .models import PeerConsent
from .remote_execution import PreparedExecution, RemoteExecutionManager, RemoteScope

ACTIVE = {"requested", "accepted", "running", "cancel_requested", "unknown"}
log = structlog.get_logger("federation_execution")


def snapshot(context):
    return AuthoritySnapshot(
        **{
            key: context[key]
            for key in (
                "authority_node_id",
                "channel_id",
                "delegation_id",
                "execution_id",
                "revision",
                "state",
            )
        }
    )


def policy_token(context, generation):
    # A binding authorization identity, not an ordinary room turn lease.
    return hashlib.sha256(
        canonical(
            [
                generation,
                *[
                    context.get(key, 0)
                    for key in (
                        "grant_epoch",
                        "peer_epoch",
                        "policy_epoch",
                        "local_peer_epoch",
                        "local_policy_epoch",
                    )
                ],
            ]
        ).encode()
    ).hexdigest()


class FederationExecutionWorker:
    def __init__(self, service, transport, *, interval=2.0):
        self.service, self.sessions, self.interval = service, service.sessions, interval
        self.manager = RemoteExecutionManager(self.sessions, transport)
        service.execution_transport = transport
        self._authorized = {}
        self._local_policies = {}
        self._offsets = {}
        self._task = None
        self._jobs = {}
        self._semaphore = asyncio.Semaphore(4)
        self.bridge = ExecutorBridge(
            self.sessions,
            node_id=service.node_id,
            manager=self.manager,
            scope_factory=RemoteScope,
            permits=self._permits,
            reauthorize=self._reauthorize,
        )

    def _permits(self, fence):
        current = self._authorized.get(RemoteScope(**fence.scope).key)
        return (
            current is not None
            and current[0] > time.monotonic()
            and current[1:] == (fence.generation, fence.lease_token, fence.grant_epoch)
        )

    async def _reauthorize(self, db, binding):
        _, peer, _ = await require_local_executor(
            self.service,
            db,
            Identity("agent", binding.agent_id),
            binding.authority_node_id,
            binding.channel_id,
        )
        if peer is not None:
            consent = await db.get(
                PeerConsent,
                (
                    binding.authority_node_id,
                    binding.authority_node_id,
                    binding.channel_id,
                ),
                populate_existing=True,
            )
            expected = self._local_policies.get(RemoteScope(**binding.scope).key)
            if consent is None or expected != (peer.epoch, consent.policy_epoch):
                raise DelegationError("SCOPE_EPOCH_CONFLICT")
        if not self._permits(self.bridge._fence(binding)):
            raise DelegationError("LOCAL_FENCE_DENIED")

    def _allow(self, value, context):
        # Fresh authority permits reading the stopped receipt again. The agent's
        # durable revoke tombstone still forbids restarting this execution.
        self.bridge._disabled.discard(value.scope.key)
        self._local_policies[value.scope.key] = (
            context.get("local_peer_epoch", 0),
            context.get("local_policy_epoch", 0),
        )
        self._authorized[value.scope.key] = (
            time.monotonic() + 20,
            value.generation,
            policy_token(context, value.generation),
            context["grant_epoch"],
        )

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="federation_execution")

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        for task in list(self._jobs.values()):
            task.cancel()
        await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)
        self._jobs.clear()
        self._authorized.clear()
        await self.bridge.close()
        await self.manager.transport.close()

    async def _run(self):
        async def loop(operation):
            while True:
                try:
                    await operation()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — next pass retries durable state
                    log.warning(
                        "worker_tick_failed",
                        code=getattr(exc, "code", type(exc).__name__),
                    )
                await asyncio.sleep(self.interval)

        # Slow/offline channel synchronization never delays active execution leases.
        async with asyncio.TaskGroup() as group:
            group.create_task(loop(self._sync_tick))
            group.create_task(loop(self._execution_tick))

    def _batch(self, name, rows, limit=64):
        if not rows:
            return []
        offset = self._offsets.get(name, 0) % len(rows)
        self._offsets[name] = (offset + limit) % len(rows)
        return (rows[offset:] + rows[:offset])[:limit]

    async def _bounded(self, fn, *args):
        async with self._semaphore:
            try:
                await fn(*args)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one denied/offline channel must not starve others
                log.info(
                    "operation_deferred",
                    operation=fn.__name__,
                    code=getattr(exc, "code", type(exc).__name__),
                )

    async def _sync_stream(self, authority, channel):
        async with self.sessions.begin() as db:
            _, grant = await self.service.mirror_policy(db, authority, channel)
            epoch = grant.epoch
            principals = [
                actor
                for actor in grant.actors
                if actor.get("node_id") == self.service.node_id
            ]
        # Use only an existing granted local principal and its current room ACL.
        for principal in principals:
            identity = Identity(
                "user" if principal["kind"] == "human" else "agent",
                principal["principal_id"],
            )
            try:
                await pull(
                    self.service,
                    identity,
                    {
                        "protocol_version": 1,
                        "sender_node_id": self.service.node_id,
                        "authority_node_id": authority,
                        "channel_id": channel,
                        "grant_epoch": epoch,
                        "actor": principal,
                    },
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — try only already granted principals
                log.debug(
                    "sync_principal_denied",
                    code=getattr(exc, "code", type(exc).__name__),
                )

    async def _retry(self, authority, channel, request_id, actor):
        if actor["node_id"] != self.service.node_id:
            return
        identity = Identity(
            "user" if actor["kind"] == "human" else "agent", actor["principal_id"]
        )
        await retry_submission(self.service, identity, authority, channel, request_id)

    async def _sync_tick(self):
        async with self.sessions() as db:
            streams = (
                await db.execute(
                    select(ChannelStream.authority_node_id, ChannelStream.channel_id)
                    .where(ChannelStream.authority_node_id != self.service.node_id)
                    .order_by(ChannelStream.authority_node_id, ChannelStream.channel_id)
                )
            ).all()
            submissions = list(
                await db.scalars(
                    select(ChannelSubmission)
                    .where(ChannelSubmission.state == "unconfirmed")
                    .order_by(ChannelSubmission.request_id)
                )
            )
        await asyncio.gather(
            *[
                self._bounded(self._sync_stream, *row)
                for row in self._batch("streams", list(streams))
            ],
            *[
                self._bounded(
                    self._retry,
                    row.authority_node_id,
                    row.channel_id,
                    row.request_id,
                    json.loads(row.body)["actor"],
                )
                for row in self._batch("submissions", submissions)
            ],
        )

    async def _execution_tick(self):
        async with self.sessions() as db:
            authorities = list(
                await db.scalars(
                    select(Delegation).where(
                        Delegation.executor_node_id == self.service.node_id,
                        Delegation.state.in_(ACTIVE),
                    )
                )
            )
            mirrors = list(
                await db.scalars(
                    select(DelegationMirror).where(DelegationMirror.state.in_(ACTIVE))
                )
            )
            bindings = list(
                await db.scalars(
                    select(ExecutorBinding).where(
                        ExecutorBinding.authority_state.in_(ACTIVE)
                        | (ExecutorBinding.local_state == "cleanup_pending")
                    )
                )
            )
        work = {
            (r.authority_node_id, r.channel_id, r.id, r.executor_agent_id)
            for r in authorities
        }
        work.update(
            (r.authority_node_id, r.channel_id, r.delegation_id, r.executor["agent_id"])
            for r in mirrors
            if r.executor.get("node_id") == self.service.node_id
        )
        work.update(
            (r.authority_node_id, r.channel_id, r.delegation_id, r.agent_id)
            for r in bindings
        )
        # A bounded set of independent jobs renews each active lease on its own
        # schedule. Pending/offline requests yield their slot; running jobs keep it.
        available = max(0, 8 - len(self._jobs))
        candidates = sorted(work - self._jobs.keys())
        for row in self._batch("executions", candidates, available):
            task = asyncio.create_task(self._job(row))
            self._jobs[row] = task
            task.add_done_callback(lambda _, key=row: self._jobs.pop(key, None))
        now = time.monotonic()
        self._authorized = {
            key: value for key, value in self._authorized.items() if value[0] > now
        }

    async def _job(self, row):
        try:
            while True:
                if await self._execute(*row):
                    return
                async with self.sessions() as db:
                    binding = await db.get(ExecutorBinding, row[2])
                if (
                    binding is None
                    or binding.local_state == "settled"
                    or binding.authority_state == "unknown"
                ):
                    return
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a later pass retries without blocking other leases
            log.info(
                "execution_deferred", code=getattr(exc, "code", type(exc).__name__)
            )

    async def _cleanup(self, binding):
        try:
            if (
                binding.scope["workspace_binding_id"]
                == f"unstarted:{binding.delegation_id}"
            ):
                await self.manager.retire_unstarted(binding)
            else:
                await self.manager.cancel(binding.execution_id)
        except KeyError:
            pass  # No agent prepare record remains; there is nothing left to start.
        async with self.bridge._transaction() as db:
            row = await db.get(ExecutorBinding, binding.delegation_id)
            if row is not None and row.local_state == "cleanup_pending":
                row.local_state = "settled"

    async def _context(self, authority, channel, delegation, agent):
        return await resolve_execution_context(
            self.service,
            authority_node_id=authority,
            channel_id=channel,
            delegation_id=delegation,
            agent_id=agent,
        )

    async def _execute(self, authority, channel, delegation, agent_id):
        async with self.sessions() as db:
            binding = await db.get(ExecutorBinding, delegation)
            agent = await db.get(Agent, agent_id)
        if agent is None:
            return True
        if binding is not None and binding.local_state == "cleanup_pending":
            await self._cleanup(binding)
            return
        try:
            context = await self._context(authority, channel, delegation, agent_id)
            if binding is not None and binding.lease_token != policy_token(
                context, binding.generation
            ):
                raise DelegationError("SCOPE_EPOCH_CONFLICT")
        except Exception:
            if binding is not None:
                scope = RemoteScope(**binding.scope)
                self._authorized.pop(scope.key, None)
                await self.bridge.revoke(scope)
            raise
        current = snapshot(context)
        if binding is None:
            if current.state not in {"requested", "cancel_requested"}:
                # Authority acceptance without our durable local binding is not
                # permission to recreate a process. Operator reconciliation needed.
                return
            execution_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"anygarden:execution:v1:{self.service.node_id}:{delegation}",
                )
            )
            if current.state == "cancel_requested" and current.execution_id is None:
                await self._cancel_unprepared(context, agent, execution_id)
                return
            value = await self.manager.prepare(
                agent_id,
                generation=agent.generation,
                execution_id=execution_id,
                execution_node_id=self.service.node_id,
                authority_node_id=authority,
                channel_id=channel,
                prompt=context["prompt"],
                policy_epoch=int.from_bytes(
                    hashlib.sha256(policy_token(context, 0).encode()).digest()[:7],
                    "big",
                ),
                grant_epoch=context["grant_epoch"],
                peer_epoch=context["peer_epoch"],
            )
            self._allow(value, context)
            await self.bridge.prepare(
                delegation,
                value,
                generation=value.generation,
                lease_token=policy_token(context, value.generation),
                grant_epoch=context["grant_epoch"],
            )
        else:
            value = await self.manager.descriptor(binding.execution_id)
            self._allow(value, context)
        if current.state == "cancel_requested":
            pending = await self.bridge.cancel(delegation, current)
            if pending:
                await self.bridge.deliver(
                    pending,
                    lambda command: send_executor_command(self.service, command),
                )
            return
        if current.state not in ACTIVE:
            # An authority commit may have succeeded before its ACK was lost.
            # Recover the exact persisted receipt before retiring local intent.
            async with self.sessions() as db:
                pending = list(
                    await db.scalars(
                        select(DelegationOutbox.id).where(
                            DelegationOutbox.delegation_id == delegation,
                            DelegationOutbox.state == "pending",
                        )
                    )
                )
            for outbox in pending:
                await self.bridge.deliver(
                    outbox, lambda command: send_executor_command(self.service, command)
                )
            async with self.bridge._transaction() as db:
                row = await self.bridge._binding(db, delegation)
                self.bridge._check_snapshot(row, current)
                row.revision, row.authority_state = current.revision, current.state
                row.local_state = "cleanup_pending"
            await self._cleanup(row)
            return
        async with self.sessions() as db:
            pending = list(
                await db.scalars(
                    select(DelegationOutbox.id).where(
                        DelegationOutbox.delegation_id == delegation,
                        DelegationOutbox.state == "pending",
                    )
                )
            )
        for outbox in pending:
            await self.bridge.deliver(
                outbox, lambda command: send_executor_command(self.service, command)
            )
        async with self.sessions() as db:
            binding = await db.get(ExecutorBinding, delegation)
        if binding.local_state == "accepted":

            async def confirm(_):
                latest = await self._context(authority, channel, delegation, agent_id)
                if policy_token(latest, value.generation) != binding.lease_token:
                    raise DelegationError("SCOPE_EPOCH_CONFLICT")
                self._allow(value, latest)
                return snapshot(latest)

            await self.bridge.launch(delegation, value, confirm)
        pending = await self.bridge.observe(delegation)
        if pending:
            await self.bridge.deliver(
                pending, lambda command: send_executor_command(self.service, command)
            )

    async def _cancel_unprepared(self, context, agent, execution_id):
        """Atomically fence any future prepare; absence of launch intent proves no start."""
        scope = RemoteScope(
            self.service.node_id,
            agent.id,
            context["authority_node_id"],
            context["channel_id"],
            None,
            f"unstarted:{context['delegation_id']}",
            0,
            context["policy_epoch"],
            agent.engine,
            "unstarted",
        )
        value = PreparedExecution(
            execution_id,
            scope,
            hashlib.sha256(canonical(context).encode()).hexdigest(),
            agent.generation,
        )
        self._allow(value, context)
        async with self.bridge._transaction() as db:
            if await db.get(ExecutorBinding, context["delegation_id"]) is not None:
                return  # Concurrent prepare won; next tick uses its actual receipt.
            binding = ExecutorBinding(
                delegation_id=context["delegation_id"],
                authority_node_id=context["authority_node_id"],
                channel_id=context["channel_id"],
                agent_id=agent.id,
                generation=agent.generation,
                lease_token=policy_token(context, agent.generation),
                grant_epoch=context["grant_epoch"],
                execution_id=execution_id,
                invocation_fingerprint=value.fingerprint,
                scope=asdict(scope),
                revision=context["revision"],
                authority_state="cancel_requested",
                local_state="cancel_requested",
            )
            await self._reauthorize(db, binding)
            db.add(binding)
            await db.flush()
            outbox = await self.bridge._enqueue(
                db, binding, "task.cancelled", process_state="not_started"
            )
        await self.bridge.deliver(
            outbox, lambda command: send_executor_command(self.service, command)
        )
