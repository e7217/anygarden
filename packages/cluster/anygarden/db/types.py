"""Custom SQLAlchemy types — timezone-safe datetime for SQLite.

Issue #93 — SQLite stores ``DateTime(timezone=True)`` without timezone
information, so aware UTC datetimes round-trip as naive. Pydantic v2
then serializes naive datetimes without a timezone designator
(``"2026-04-17T05:12:03.456789"``), and ECMAScript parses such strings
as *local* time. In KST(+9) clients every timestamp shifts nine hours
into the past, breaking the pending-chip TTL filter and miscolouring
message timestamps.

``UtcDateTime`` restores the invariant: values read from the database
always carry ``tzinfo=UTC``. Pydantic then emits ``+00:00`` and JS
Date parses the instant correctly. PostgreSQL already preserves
timezone information, so this decorator is a no-op there.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """``DateTime(timezone=True)`` that guarantees aware UTC on read.

    - ``process_bind_param``: naive input is assumed UTC and tagged
      before handing to the driver. Aware input passes through.
    - ``process_result_value``: naive DB values (SQLite) are tagged
      as UTC so downstream code never sees a naive datetime.

    The policy of promoting naive input to UTC (rather than rejecting
    it) keeps existing fixtures and migration scripts working — they
    were always intended to represent UTC instants per the project's
    ``_utcnow()`` convention.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def process_result_value(
        self, value: Any, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
