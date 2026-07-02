"""#510 — ``send_sync_batch`` must commit minted anygarden self-MCP tokens.

Regression guard: ``send_sync_batch`` mints the per-agent
``anygarden_mcp_token`` inside ``_build_sync_frame`` (``db.add`` of an
``AgentToken`` row) but historically returned without committing. The
frame — carrying that *uncommitted* token — was still shipped to the
daemon and injected into the agent as ``ANYGARDEN_AGENT_TOKEN``, so
every ``/mcp/rpc`` call (incl. ``mark_task_status``) 401'd with
"Invalid agent token". The sibling paths ``request_start`` and
``handle_token_request`` both commit; only the batch path did not.

Two invariants locked here:
1. The token shipped in a ``sync_batch`` frame is persisted in
   ``agent_tokens`` (so MCP auth accepts it).
2. Repeated ``send_sync_batch`` calls within one scheduler lifetime are
   idempotent — the durable ``_token_cache`` (populated only via the
   ``after_commit`` hook) makes the second call reuse the same token
   instead of re-minting a fresh, different one.

The existing ``test_declarative_reconcile.py::test_send_sync_batch``
does not catch this because it constructs the scheduler *without*
``cluster_external_url``, so no MCP token is minted at all.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentToken, Base, Machine, User
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus


class FakeWS:
    """Captures frames sent to the machine WebSocket."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    def last_frame(self) -> dict:
        return json.loads(self.sent[-1])


@pytest_asyncio.fixture()
async def batch_env():
    """In-memory DB with one codex-cli agent placed on an online machine,
    and a scheduler configured with ``cluster_external_url`` so the
    anygarden self-MCP token is actually minted into the spawn frame."""
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(
        db_factory=factory,
        machine_bus=bus,
        cluster_external_url="http://localhost:8001",
    )
    fake_ws = FakeWS()

    async with factory() as db:
        user = User(email="admin@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        machine = Machine(
            name="batch-machine",
            hostname="host-batch",
            owner_user_id=user.id,
            status="online",
            max_agents=5,
        )
        db.add(machine)
        await db.flush()

        agent = Agent(
            name="codex-agent",
            engine="codex-cli",
            desired_state="running",
            actual_state="idle",
            placed_on_machine_id=machine.id,
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)

        machine_id = machine.id
        agent_id = agent.id

    await bus.register(machine_id, fake_ws)

    yield {
        "factory": factory,
        "lifecycle": lifecycle,
        "machine_id": machine_id,
        "agent_id": agent_id,
        "fake_ws": fake_ws,
    }

    await engine.dispose()


def _agent_frame(fake_ws: FakeWS, agent_id: str) -> dict:
    batch = fake_ws.last_frame()
    assert batch["type"] == "sync_batch"
    for frame in batch["agents"]:
        if frame["agent_id"] == agent_id:
            return frame
    raise AssertionError(f"agent {agent_id} not in sync_batch frame")


async def _count_tokens(factory, agent_id: str) -> int:
    async with factory() as db:
        rows = (
            await db.execute(
                select(AgentToken).where(AgentToken.agent_id == agent_id)
            )
        ).scalars().all()
    return len(rows)


class TestSendSyncBatchTokenCommit:
    @pytest.mark.asyncio
    async def test_batch_persists_mcp_token(self, batch_env) -> None:
        """The ``anygarden_mcp_token`` shipped in the batch must be
        persisted in ``agent_tokens`` — otherwise the agent 401s at
        ``/mcp/rpc``. RED before the fix: 0 rows committed."""
        lifecycle = batch_env["lifecycle"]

        await lifecycle.send_sync_batch(batch_env["machine_id"])

        frame = _agent_frame(batch_env["fake_ws"], batch_env["agent_id"])
        token = frame["anygarden_mcp_token"]
        assert token, "codex agent frame must carry a non-empty MCP token"

        count = await _count_tokens(batch_env["factory"], batch_env["agent_id"])
        assert count >= 1, (
            "send_sync_batch must COMMIT the minted AgentToken row so MCP "
            "auth accepts the token; found 0 persisted rows (uncommitted)"
        )

    @pytest.mark.asyncio
    async def test_batch_is_idempotent(self, batch_env) -> None:
        """Repeated batches within one scheduler lifetime reuse the same
        committed token (cache hit) — no re-mint, no duplicate row.
        RED before the fix: cache never populates (no commit → no
        ``after_commit``), so each call mints a *different* token."""
        lifecycle = batch_env["lifecycle"]

        await lifecycle.send_sync_batch(batch_env["machine_id"])
        token1 = _agent_frame(
            batch_env["fake_ws"], batch_env["agent_id"]
        )["anygarden_mcp_token"]

        await lifecycle.send_sync_batch(batch_env["machine_id"])
        token2 = _agent_frame(
            batch_env["fake_ws"], batch_env["agent_id"]
        )["anygarden_mcp_token"]

        assert token1 == token2, (
            "second batch must reuse the cached committed token, not mint "
            "a fresh one (idempotency)"
        )
        count = await _count_tokens(batch_env["factory"], batch_env["agent_id"])
        assert count == 1, (
            f"exactly one AgentToken row expected across two batches, "
            f"got {count}"
        )
