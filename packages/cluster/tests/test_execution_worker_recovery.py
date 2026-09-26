"""File-DB recovery and scheduling under injected transport faults.

The authority harness substitutes only peer delivery/grant admission. Executor
SQL/outbox commit, restarted worker discovery and manager descriptor lookup are
real; no engine process is needed for a prepared-execution cleanup proof.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from dataclasses import asdict
from itertools import pairwise
from types import SimpleNamespace

import pytest
from anygarden.db.models import Agent
from anygarden.federation.delegation_models import ExecutorBinding
from anygarden.federation.execution_transport import ExecutionTransportError
from anygarden.federation.execution_worker import FederationExecutionWorker
from anygarden.federation.executor import ExecutorBridge
from anygarden.federation.remote_execution import PreparedExecution, RemoteScope
from sqlalchemy import select

from .test_federation_delegation import h as _h

recovery_db = _h


class FaultTransport:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    async def request(self, agent_id, action, payload, **kwargs):
        self.calls.append((agent_id, action, payload.copy()))
        if self.failures:
            self.failures -= 1
            raise ExecutionTransportError("EXECUTION_TIMEOUT")
        assert action == "cancel"
        return {
            "execution_id": payload["execution_id"],
            "state": "cancelled",
            "process_state": "not_started",
            "outcome": "cancelled",
        }

    async def close(self):
        pass


def descriptor(h):
    return PreparedExecution(
        h.execution,
        RemoteScope(
            h.node,
            h.agent,
            h.authority,
            h.channel,
            None,
            "opaque-managed-workspace",
            1,
            1,
        ),
        "a" * 64,
        1,
    )


def worker(h, transport, interval=0.02):
    return FederationExecutionWorker(
        SimpleNamespace(sessions=h.sessions, node_id=h.node),
        transport,
        interval=interval,
    )


async def test_terminal_outbox_commit_survives_crash_and_cleanup_timeout(recovery_db):
    h = recovery_db
    await h.commit(h.command("task.request", 0))
    value = descriptor(h)

    async def authorize(db, _binding):
        await h.auth(db, {})

    bridge = ExecutorBridge(
        h.sessions,
        node_id=h.node,
        manager=SimpleNamespace(),
        scope_factory=RemoteScope,
        permits=lambda _: True,
        reauthorize=authorize,
    )

    async def suppressed(_):
        return True

    bridge._self_suppressed = suppressed
    outbox = await bridge.prepare(
        h.delegation, value, generation=1, lease_token="lease", grant_epoch=1
    )
    await bridge.deliver(outbox, h.commit)
    # Simulate coordinator death immediately after committing the terminal ACK:
    # no in-memory callback or old worker is allowed to arrange the cleanup.
    async with h.sessions() as db:
        binding = await db.get(ExecutorBinding, h.delegation)
        assert binding.authority_state == "rejected"
        assert binding.local_state == "cleanup_pending"
        db.add(Agent(id=h.agent, name="worker", engine="codex-cli", generation=1))
        await db.commit()
    transport = FaultTransport(failures=1)
    restarted = worker(h, transport)
    try:
        await restarted._execution_tick()
        await asyncio.gather(*list(restarted._jobs.values()))
        async with h.sessions() as db:
            binding = await db.get(ExecutorBinding, h.delegation)
            assert binding.local_state == "cleanup_pending"
        await restarted._execution_tick()
        await asyncio.gather(*list(restarted._jobs.values()))
        async with h.sessions() as db:
            binding = await db.get(ExecutorBinding, h.delegation)
            assert binding.local_state == "settled"
        assert len(transport.calls) == 2
        assert transport.calls[0] == transport.calls[1]
        assert transport.calls[0][2] == {
            "execution_id": h.execution,
            "fingerprint": "a" * 64,
        }
    finally:
        await restarted.close()


async def test_blocked_shared_sync_does_not_delay_active_execution_renewal(recovery_db):
    h = recovery_db
    value = descriptor(h)
    async with h.sessions() as db:
        db.add(Agent(id=h.agent, name="worker", engine="codex-cli", generation=1))
        db.add(
            ExecutorBinding(
                delegation_id=h.delegation,
                authority_node_id=h.authority,
                channel_id=h.channel,
                agent_id=h.agent,
                generation=1,
                lease_token="lease",
                grant_epoch=1,
                execution_id=h.execution,
                invocation_fingerprint=value.fingerprint,
                scope=asdict(value.scope),
                revision=3,
                authority_state="running",
                local_state="launch_intent",
            )
        )
        await db.commit()
    running = worker(h, FaultTransport())
    sync_started, release_sync, renewed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    times = []

    async def stalled_sync():
        sync_started.set()
        await release_sync.wait()

    async def renew(*_):
        times.append(time.monotonic())
        if len(times) >= 5:
            renewed.set()

    # These doubles represent an unresponsive peer sync request and a fresh,
    # successful execution-context+receipt exchange. The scheduler, independent
    # job, real DB discovery and shutdown paths are the product implementation.
    running._sync_tick = stalled_sync
    running._execute = renew
    running.start()
    try:
        async with asyncio.timeout(2):
            await sync_started.wait()
            await renewed.wait()
        assert not release_sync.is_set()
        assert max(b - a for a, b in pairwise(times)) < 0.5
        assert len(running._jobs) == 1
    finally:
        await running.close()
    assert not running._jobs


async def test_cleanup_resumes_without_authority_regrant(recovery_db):
    h = recovery_db
    value = descriptor(h)
    async with h.sessions() as db:
        db.add(Agent(id=h.agent, name="worker", engine="codex-cli", generation=1))
        db.add(
            ExecutorBinding(
                delegation_id=h.delegation,
                authority_node_id=h.authority,
                channel_id=h.channel,
                agent_id=h.agent,
                generation=1,
                lease_token="expired",
                grant_epoch=1,
                execution_id=h.execution,
                invocation_fingerprint=value.fingerprint,
                scope=asdict(value.scope),
                revision=3,
                authority_state="cancelled",
                local_state="cleanup_pending",
            )
        )
        await db.commit()
    transport = FaultTransport()
    restarted = worker(h, transport)

    async def denied_context(*_):
        pytest.fail("Cleanup cannot require a new execution grant to stop old work")

    restarted._context = denied_context
    try:
        await restarted._execution_tick()
        await asyncio.gather(*list(restarted._jobs.values()))
        async with h.sessions() as db:
            assert await db.scalar(select(ExecutorBinding.local_state)) == "settled"
        assert len(transport.calls) == 1
    finally:
        await restarted.close()


def test_server_only_construction_never_imports_agent_runtime():
    script = r"""
import importlib.abc, sys
from types import SimpleNamespace
class BlockAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "anygarden_agent" or fullname.startswith("anygarden_agent."):
            raise ImportError("Agent package is not installed on this server")
sys.meta_path.insert(0, BlockAgent())
from anygarden.ws.manager import ConnectionManager
from anygarden.federation.execution_transport import ExecutionTransport
from anygarden.federation.execution_worker import FederationExecutionWorker
from anygarden.federation.remote_execution import RemoteExecutionManager
transport = ExecutionTransport(None, ConnectionManager())
worker = FederationExecutionWorker(SimpleNamespace(sessions=None, node_id="server"), transport)
assert isinstance(worker.bridge.manager, RemoteExecutionManager)
assert not any(key == "anygarden_agent" or key.startswith("anygarden_agent.") for key in sys.modules)
print("server-only")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "server-only"
