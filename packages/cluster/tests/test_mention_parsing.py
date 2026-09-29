"""Tests for ID-based mention parsing (upgraded parse_mentions)."""

from __future__ import annotations

from anygarden.orchestration.rules import parse_mentions


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


# ── @everyone (#739) ─────────────────────────────────────────────────


def test_parse_everyone_alone():
    assert parse_mentions("@everyone 오늘 회의 몇 시?") == [{"type": "everyone"}]


def test_parse_everyone_is_recognised_alongside_id_tokens():
    result = parse_mentions("<@user:a1> and @everyone take a look")
    assert result == [{"type": "user", "id": "a1"}, {"type": "everyone"}]


def test_parse_everyone_is_not_a_legacy_mention():
    result = parse_mentions("@everyone and @Alice")
    assert result == [{"type": "everyone"}, {"type": "legacy", "name": "Alice"}]


def test_parse_everyone_deduplicated():
    assert parse_mentions("@everyone @everyone hi") == [{"type": "everyone"}]


def test_parse_everyone_requires_word_boundaries():
    assert parse_mentions("@everyone123 hi") == [
        {"type": "legacy", "name": "everyone123"}
    ]
    assert parse_mentions("mail a@everyone.com") == []
    assert parse_mentions("@everyone-bot hi") == [
        {"type": "legacy", "name": "everyone-bot"}
    ]


def test_parse_everyone_is_lowercase_only():
    assert parse_mentions("@Everyone hi") == [{"type": "legacy", "name": "Everyone"}]


def test_parse_everyone_at_sentence_end():
    assert parse_mentions("thanks @everyone.") == [{"type": "everyone"}]
