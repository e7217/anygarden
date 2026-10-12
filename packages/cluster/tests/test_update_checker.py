"""SYS-06 — background update check refreshes the same cache as the manual check.

No test here touches the network: ``version_service.fetch_pypi_latest`` is
stubbed, and the conftest pins ``ANYGARDEN_UPDATE_CHECK_INTERVAL_HOURS=0``
so apps built elsewhere in the suite never start the loop.
"""

from __future__ import annotations

import asyncio
import secrets

import pytest
import pytest_asyncio

from anygarden.app import _shutdown_server, create_app
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base
from anygarden.system import update_checker, version_service, version_store


@pytest_asyncio.fixture()
async def factory(tmp_path):
    # File DB, not ``:memory:`` — the in-memory engine shares one connection
    # across sessions, so a concurrent reader's rollback races the loop (#758).
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'updates.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield build_session_factory(engine)
    finally:
        await engine.dispose()


async def _rows(factory) -> dict:
    async with factory() as db:
        return {r.package: r for r in await version_store.get_all(db)}


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest.mark.req("SYS-06")
def test_setting_defaults_to_daily_and_test_env_disables_it(monkeypatch) -> None:
    monkeypatch.delenv("ANYGARDEN_UPDATE_CHECK_INTERVAL_HOURS", raising=False)
    assert AnygardenSettings().update_check_interval_hours == 24.0
    monkeypatch.setenv("ANYGARDEN_UPDATE_CHECK_INTERVAL_HOURS", "0")
    assert AnygardenSettings().update_check_interval_hours == 0.0


@pytest.mark.req("SYS-06")
async def test_loop_fills_cache_periodically(factory, monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch(package: str, **_):
        calls.append(package)
        return "99.0.0"

    monkeypatch.setattr(version_service, "fetch_pypi_latest", fake_fetch)
    task = asyncio.create_task(
        update_checker.run_update_check_loop(
            factory, interval_seconds=0.02, warmup_seconds=0
        )
    )
    try:
        await _wait_for(lambda: _async_true(len(calls) >= 2))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    row = (await _rows(factory))["anygarden"]
    assert row.latest_version == "99.0.0"
    assert row.error is None
    assert row.checked_at is not None


async def _async_true(value: bool) -> bool:
    return value


@pytest.mark.req("SYS-06")
async def test_loop_records_error_and_survives_exceptions(factory, monkeypatch) -> None:
    attempts = 0

    async def flaky_fetch(package: str, **_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")  # unexpected crash must not kill the loop
        # later attempts fall through to ``None`` = unreachable PyPI

    monkeypatch.setattr(version_service, "fetch_pypi_latest", flaky_fetch)
    task = asyncio.create_task(
        update_checker.run_update_check_loop(
            factory, interval_seconds=0.02, warmup_seconds=0
        )
    )

    async def recorded() -> bool:
        row = (await _rows(factory)).get("anygarden")
        return row is not None and row.error == "unreachable"

    try:
        await _wait_for(recorded)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.req("SYS-06")
async def test_lifespan_starts_and_stops_the_loop(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    async def fake_fetch(package: str, **_):
        return "99.0.0"

    monkeypatch.setattr(version_service, "fetch_pypi_latest", fake_fetch)
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
        update_check_interval_hours=24,
    )
    app = create_app(config)
    update_checker.start_update_check(app)
    task = app.state.update_check_task
    assert isinstance(task, asyncio.Task) and not task.done()

    await _shutdown_server(app, engine_provided=True)
    assert task.done()


@pytest.mark.req("SYS-06")
async def test_disabled_when_interval_is_zero() -> None:
    from cryptography.fernet import Fernet

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
        update_check_interval_hours=0,
    )
    app = create_app(config)
    update_checker.start_update_check(app)
    assert getattr(app.state, "update_check_task", None) is None


@pytest.mark.req("SYS-06")
async def test_manual_check_shares_the_same_writer(factory, monkeypatch) -> None:
    async def fake_fetch(package: str, **_):
        return None

    monkeypatch.setattr(version_service, "fetch_pypi_latest", fake_fetch)
    async with factory() as db:
        await update_checker.check_and_store(db)
        await db.commit()
    row = (await _rows(factory))["anygarden"]
    assert row.error == "unreachable"
