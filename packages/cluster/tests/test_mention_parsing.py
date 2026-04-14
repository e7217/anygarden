"""Tests for ID-based mention parsing (upgraded parse_mentions)."""

from __future__ import annotations

from doorae.orchestration.rules import parse_mentions


def test_parse_id_based_user_mention():
    result = parse_mentions("Hello <@user:abc123> check this")
    assert result == [{"type": "user", "id": "abc123"}]


def test_parse_id_based_room_mention():
    result = parse_mentions("See <#room:xyz789> for details")
    assert result == [{"type": "room", "id": "xyz789"}]


def test_parse_mixed_mentions():
    result = parse_mentions("<@user:a1> said check <#room:r2>")
    assert result == [
        {"type": "user", "id": "a1"},
        {"type": "room", "id": "r2"},
    ]


def test_parse_no_mentions():
    result = parse_mentions("Just a normal message")
    assert result == []


def test_parse_legacy_at_mention():
    """기존 @Name 형식은 하위호환을 위해 legacy dict로 반환."""
    result = parse_mentions("Hey @Alice")
    assert result == [{"type": "legacy", "name": "Alice"}]


def test_parse_mixed_id_and_legacy_drops_legacy():
    """When ID-based mentions are present, legacy @Name is treated as plain text.

    This is intentional: ID-based tokens are inserted by the autocomplete UI,
    so bare @Name in the same message is just regular text, not a mention.
    """
    result = parse_mentions("<@user:abc123> please also check @Alice's report")
    assert result == [{"type": "user", "id": "abc123"}]
