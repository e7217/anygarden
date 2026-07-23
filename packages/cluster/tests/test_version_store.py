"""Tests for the version_check cache store (#546)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from anygarden.system import version_store


@pytest.mark.asyncio
async def test_upsert_creates_row(db) -> None:
    checked = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    await version_store.upsert(
        db, package="anygarden", latest_version="0.16.0", checked_at=checked
    )
    rows = await version_store.get_all(db)
    assert len(rows) == 1
    assert rows[0].package == "anygarden"
    assert rows[0].latest_version == "0.16.0"
    assert rows[0].error is None


@pytest.mark.asyncio
async def test_upsert_updates_existing(db) -> None:
    t1 = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 23, 13, 0, tzinfo=timezone.utc)
    await version_store.upsert(db, package="anygarden", latest_version="0.16.0", checked_at=t1)
    await version_store.upsert(db, package="anygarden", latest_version="0.17.0", checked_at=t2)
    rows = await version_store.get_all(db)
    assert len(rows) == 1  # same package → one row, not two
    assert rows[0].latest_version == "0.17.0"


@pytest.mark.asyncio
async def test_upsert_error_preserves_last_known_latest(db) -> None:
    """A failed check records the error but keeps the last-known latest."""
    t1 = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 23, 13, 0, tzinfo=timezone.utc)
    await version_store.upsert(db, package="anygarden", latest_version="0.16.0", checked_at=t1)
    await version_store.upsert(
        db, package="anygarden", latest_version=None, checked_at=t2, error="unreachable"
    )
    rows = await version_store.get_all(db)
    assert rows[0].latest_version == "0.16.0"  # preserved
    assert rows[0].error == "unreachable"
    assert rows[0].checked_at == t2


@pytest.mark.asyncio
async def test_get_all_empty(db) -> None:
    assert await version_store.get_all(db) == []
