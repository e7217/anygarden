"""Gateway-free usage recording regressions (task #92, #655).

The usage stream must keep working after the LLM gateway is removed: the WS
handler's CLI-engine path and the budget ledger depend only on the neutral
``anygarden.usage`` module, never on ``anygarden.llm_gateway``.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
import textwrap

from anygarden.usage import write_usage_row


def test_ws_handler_import_does_not_pull_gateway():
    """ws.handler must be importable without anygarden.llm_gateway in sys.modules."""
    code = textwrap.dedent(
        """
        import sys
        import anygarden.ws.handler  # noqa: F401
        leaked = [m for m in sys.modules if m.startswith("anygarden.llm_gateway")]
        assert not leaked, f"ws.handler pulled gateway modules: {leaked}"
        import anygarden.usage  # noqa: F401
        print("WS-IMPORT-ISOLATION OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "WS-IMPORT-ISOLATION OK" in result.stdout


async def test_write_usage_row_persists_row(tmp_path):
    """The neutral writer persists a row via any session factory."""

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            self.committed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeFactory:
        def __init__(self, session):
            self.session = session

        def __call__(self):
            return self.session

    session = FakeSession()
    factory = FakeFactory(session)

    await write_usage_row(
        factory,
        identity_kind="agent",
        identity_id="agent-1",
        agent_id="agent-1",
        model_name="glm-5.3-flash",
        prompt_tokens=10,
        completion_tokens=5,
        duration_ms=123,
        status_code=200,
        room_id="room-1",
    )
    assert session.added, "writer did not add a UsageLedger row"
    assert session.committed is True


async def test_ws_write_feeds_real_ledger_sum(tmp_path):
    """End-to-end: WS handler's writer -> real SQLite -> budget ledger SUM.

    Uses the real UsageLedger table and the real compute_observed_tokens —
    no gateway code anywhere — proving the gateway-free CLI path still
    feeds budgets after the #655 split.
    """
    import secrets

    from anygarden.budgets.ledger import compute_observed_tokens
    from anygarden.config import AnygardenSettings
    from anygarden.db.engine import build_engine, build_session_factory
    from anygarden.db.models import Agent, Base, Project, Room, TokenBudgetPolicy

    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    fac = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with fac() as db:
        agent = Agent(id="a1", name="A1", engine="pi-cli")
        project = Project(id="p1", name="P1")
        db.add_all([agent, project])
        await db.flush()
        db.add(Room(id="r1", project_id="p1", name="R1"))
        db.add(
            TokenBudgetPolicy(
                id="pol-1",
                scope_type="agent",
                scope_id="a1",
                window_kind="24h",
                token_ceiling=1000,
                is_active=True,
            )
        )
        await db.commit()

    # Two WS-handler-shaped writes through the neutral writer.
    for prompt, completion in ((10, 2), (20, 3)):
        await write_usage_row(
            fac,
            identity_kind="agent",
            identity_id="a1",
            agent_id="a1",
            model_name="glm-5.3-flash",
            prompt_tokens=prompt,
            completion_tokens=completion,
            duration_ms=100,
            status_code=200,
            room_id="r1",
        )
    # A failed call must not feed the budget window (status >= 400 filtered).
    await write_usage_row(
        fac,
        identity_kind="agent",
        identity_id="a1",
        agent_id="a1",
        model_name="glm-5.3-flash",
        prompt_tokens=999,
        completion_tokens=999,
        duration_ms=5,
        status_code=500,
        room_id="r1",
    )

    window_start = datetime.now(UTC) - timedelta(hours=24)
    async with fac() as db:
        observed = await compute_observed_tokens(
            db, scope_type="agent", scope_id="a1", window_start=window_start
        )
    assert observed == (10 + 2) + (20 + 3)  # failed 999-row excluded
    await engine.dispose()


def test_writer_handles_db_failure_silently():
    """A DB hiccup must not raise out of the writer (fire-and-forget contract)."""
    import asyncio

    class ExplodingFactory:
        def __call__(self):
            raise RuntimeError("db hiccup")

    async def run():
        await write_usage_row(
            ExplodingFactory(),
            identity_kind="agent",
            identity_id="a",
            agent_id=None,
            model_name="m",
            prompt_tokens=None,
            completion_tokens=None,
            duration_ms=1,
            status_code=500,
        )

    asyncio.run(run())  # must not raise
