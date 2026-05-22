"""Unit tests for the FTS ``created_at`` → ISO normalizer (#93)."""

from __future__ import annotations

from anygarden.api.v1.search import _fts_created_at_to_iso


class TestFtsCreatedAtToIso:
    def test_sqlite_datetime_format_becomes_utc_iso(self) -> None:
        assert (
            _fts_created_at_to_iso("2026-04-17 05:12:03.456789")
            == "2026-04-17T05:12:03.456789+00:00"
        )

    def test_iso_without_tz_becomes_utc(self) -> None:
        assert (
            _fts_created_at_to_iso("2026-04-17T05:12:03")
            == "2026-04-17T05:12:03+00:00"
        )

    def test_iso_with_tz_passes_through(self) -> None:
        assert (
            _fts_created_at_to_iso("2026-04-17T05:12:03+00:00")
            == "2026-04-17T05:12:03+00:00"
        )

    def test_empty_string(self) -> None:
        assert _fts_created_at_to_iso("") == ""

    def test_none(self) -> None:
        assert _fts_created_at_to_iso(None) == ""

    def test_unparseable_returns_raw(self) -> None:
        # Defensive: don't crash on unexpected shapes.
        assert _fts_created_at_to_iso("not-a-date") == "not-a-date"
