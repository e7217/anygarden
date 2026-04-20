"""Unit tests for :class:`doorae.llm_gateway.supervisor.LLMGatewaySupervisor` (#197).

Six scenarios cover the state machine's observable behaviour — spawn,
health-failure, crash auto-respawn, backoff exhaustion, admin restart,
and clean stop. Sub-second backoff / grace timings are injected so the
full suite finishes in under a second; real timings are validated via
the defaults at construction time.

The supervisor does not touch the network or the filesystem — it
consumes a ``spawn_fn`` (subprocess-exec substitute) and a
``health_probe`` (HTTP-health substitute), both injected at construction.
Production wiring lives in ``doorae.app`` where these are bound to
``asyncio.create_subprocess_exec`` and an ``httpx.AsyncClient``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from doorae.llm_gateway.supervisor import (
    GatewayState,
    LLMGatewaySupervisor,
    _SpawnParams,
)


def _make_params() -> _SpawnParams:
    return _SpawnParams(
        config_path=Path("/tmp/doorae-test-litellm.yaml"),
        child_env={"DOORAE_LITELLM_TEST_KEY": "val"},
        master_key="sk-test-master",
        port=4001,
    )


class FakeProc:
    """Stand-in for ``asyncio.subprocess.Process``.

    The supervisor reads ``.pid``, ``.returncode``, and calls
    ``.terminate()``/``.kill()``/``.wait()``. ``exit_with`` simulates
    the child exiting — either voluntarily (tests call it directly) or
    in response to SIGTERM (tests rebind ``.terminate`` to invoke it).
    """

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self._wait_event = asyncio.Event()

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        assert self.returncode is not None
        return self.returncode

    def exit_with(self, code: int) -> None:
        self.returncode = code
        self._wait_event.set()


def _rebind_terminate_to_exit(proc: FakeProc, code: int = 0) -> None:
    """Make the fake proc honour SIGTERM by exiting cleanly."""
    original = proc.terminate

    def terminate_and_exit() -> None:
        original()
        proc.exit_with(code)

    proc.terminate = terminate_and_exit  # type: ignore[method-assign]


# ── start() ────────────────────────────────────────────────────────────


class TestStart:
    async def test_successful_start_reaches_running(self) -> None:
        proc = FakeProc(pid=4242)
        spawn_fn = AsyncMock(return_value=proc)
        health_probe = AsyncMock(return_value=True)

        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=spawn_fn,
            health_probe=health_probe,
        )

        await sup.start()
        try:
            assert sup.state == GatewayState.RUNNING
            spawn_fn.assert_awaited_once()
            health_probe.assert_awaited()
            status = sup.status()
            assert status.pid == 4242
            assert status.port == 4001
            assert status.state == GatewayState.RUNNING
        finally:
            _rebind_terminate_to_exit(proc)
            await sup.stop()

    async def test_health_failure_transitions_to_failed(self) -> None:
        proc = FakeProc()
        spawn_fn = AsyncMock(return_value=proc)
        health_probe = AsyncMock(return_value=False)

        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=spawn_fn,
            health_probe=health_probe,
        )

        # Pre-exit the fake proc so graceful-terminate during the
        # failure path doesn't deadlock on wait().
        proc.exit_with(1)

        await sup.start()

        assert sup.state == GatewayState.FAILED
        status = sup.status()
        assert status.last_error is not None


# ── crash handling ─────────────────────────────────────────────────────


class TestCrashHandling:
    async def test_crash_triggers_automatic_respawn(self) -> None:
        procs = [FakeProc(pid=1), FakeProc(pid=2)]
        spawn_fn = AsyncMock(side_effect=procs)
        health_probe = AsyncMock(return_value=True)

        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=spawn_fn,
            health_probe=health_probe,
            backoff_schedule=(0.001, 0.001, 0.001),
        )

        await sup.start()
        assert sup.state == GatewayState.RUNNING

        # First process crashes — supervisor should respawn.
        procs[0].exit_with(code=1)

        # Give the watch task one event-loop cycle to notice + respawn.
        for _ in range(100):
            await asyncio.sleep(0.01)
            if sup.status().pid == 2:
                break

        assert sup.state == GatewayState.RUNNING
        assert spawn_fn.await_count == 2
        assert sup.status().pid == 2
        assert sup.status().crash_count == 1

        _rebind_terminate_to_exit(procs[1])
        await sup.stop()

    async def test_four_crashes_exhaust_backoff_to_failed(self) -> None:
        # 3 backoff slots => start + 3 respawns = 4 spawns total.
        # The 4th crash finds no slot left and the supervisor gives up.
        procs = [FakeProc(pid=i) for i in range(4)]
        spawn_fn = AsyncMock(side_effect=procs)
        health_probe = AsyncMock(return_value=True)

        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=spawn_fn,
            health_probe=health_probe,
            backoff_schedule=(0.001, 0.001, 0.001),
        )

        await sup.start()

        for i in range(4):
            procs[i].exit_with(code=1)
            # Wait for watch task to react.
            for _ in range(100):
                await asyncio.sleep(0.01)
                state = sup.state
                # Either respawned (pid > i) or reached FAILED.
                if state == GatewayState.FAILED:
                    break
                if sup.status().pid is not None and sup.status().pid > i:
                    break

        assert sup.state == GatewayState.FAILED
        assert spawn_fn.await_count == 4


# ── restart() ──────────────────────────────────────────────────────────


class TestRestart:
    async def test_restart_terminates_and_spawns_new_process(self) -> None:
        procs = [FakeProc(pid=1), FakeProc(pid=2)]
        spawn_fn = AsyncMock(side_effect=procs)
        health_probe = AsyncMock(return_value=True)

        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=spawn_fn,
            health_probe=health_probe,
            graceful_shutdown=0.1,
        )

        await sup.start()
        assert sup.state == GatewayState.RUNNING

        _rebind_terminate_to_exit(procs[0])
        await sup.restart()

        try:
            assert sup.state == GatewayState.RUNNING
            assert procs[0].terminated
            assert not procs[0].killed  # honoured SIGTERM, no escalation
            assert sup.status().pid == 2
            assert spawn_fn.await_count == 2
        finally:
            _rebind_terminate_to_exit(procs[1])
            await sup.stop()


# ── stop() ─────────────────────────────────────────────────────────────


class TestStop:
    async def test_stop_marks_terminated(self) -> None:
        proc = FakeProc()
        spawn_fn = AsyncMock(return_value=proc)
        health_probe = AsyncMock(return_value=True)

        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=spawn_fn,
            health_probe=health_probe,
            graceful_shutdown=0.1,
        )

        await sup.start()
        _rebind_terminate_to_exit(proc)

        await sup.stop()

        assert sup.state == GatewayState.TERMINATED
        assert proc.terminated

    async def test_stop_when_not_started_is_noop(self) -> None:
        sup = LLMGatewaySupervisor(
            spawn_params_factory=_make_params,
            spawn_fn=AsyncMock(),
            health_probe=AsyncMock(),
        )

        await sup.stop()

        assert sup.state == GatewayState.TERMINATED
