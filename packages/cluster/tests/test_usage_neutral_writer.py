"""Gateway-free usage recording regressions (task #92, #655).

The usage stream must keep working after the LLM gateway is removed: the WS
handler's CLI-engine path and the budget ledger depend only on the neutral
``anygarden.usage`` module, never on ``anygarden.llm_gateway``.
"""

from __future__ import annotations

import subprocess
import sys
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
