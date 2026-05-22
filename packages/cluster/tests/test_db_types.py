"""Tests for custom SQLAlchemy types — ``UtcDateTime``."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Integer, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from anygarden.db.types import UtcDateTime


class _Base(DeclarativeBase):
    pass


class _Row(_Base):
    __tablename__ = "utc_dt_probe"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    when: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


@pytest.fixture()
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _save_and_load(factory, dt: datetime | None) -> datetime | None:
    # Write and read in separate sessions so the second SELECT doesn't
    # hit the identity-map cache and genuinely round-trips through
    # ``process_result_value``.
    async with factory() as writer:
        row = _Row(when=dt)
        writer.add(row)
        await writer.commit()
        pk = row.id
    async with factory() as reader:
        fetched = (
            await reader.execute(select(_Row).where(_Row.id == pk))
        ).scalar_one()
        return fetched.when


class TestUtcDateTime:
    @pytest.mark.asyncio
    async def test_aware_utc_roundtrip(self, factory) -> None:
        """Aware UTC datetime survives a SQLite round-trip with tzinfo intact."""
        original = datetime(2026, 4, 17, 5, 12, 3, 456789, tzinfo=timezone.utc)
        loaded = await _save_and_load(factory, original)
        assert loaded is not None
        assert loaded.tzinfo is not None
        # Same instant in UTC — SQLite stores microsecond precision.
        assert loaded == original

    @pytest.mark.asyncio
    async def test_naive_input_is_promoted_to_utc(self, factory) -> None:
        """Naive input is stored as UTC (policy: assume UTC) and loaded as aware."""
        naive = datetime(2026, 4, 17, 5, 12, 3, 456789)
        loaded = await _save_and_load(factory, naive)
        assert loaded is not None
        assert loaded.tzinfo is not None
        assert loaded.utcoffset().total_seconds() == 0
        # Wall-clock components preserved.
        assert (loaded.year, loaded.month, loaded.day) == (2026, 4, 17)
        assert (loaded.hour, loaded.minute, loaded.second) == (5, 12, 3)

    @pytest.mark.asyncio
    async def test_none_passes_through(self, factory) -> None:
        loaded = await _save_and_load(factory, None)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_isoformat_contains_tz_designator(self, factory) -> None:
        """Loaded values serialize with a timezone designator — the #93 invariant.

        Pydantic v2 emits ``+00:00`` for aware datetimes; we only require
        that the ISO string carries a designator (missing designator is
        the bug). This test guards against ``UtcDateTime`` regressing to
        a no-op.
        """
        original = datetime(2026, 4, 17, 5, 12, 3, tzinfo=timezone.utc)
        loaded = await _save_and_load(factory, original)
        iso = loaded.isoformat()
        assert iso.endswith("+00:00") or iso.endswith("Z")
