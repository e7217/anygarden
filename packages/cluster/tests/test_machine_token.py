"""Tests for machine token generation, hashing, and verification."""

from __future__ import annotations

from anygarden.auth.machine_token import (
    generate_machine_token,
    hash_machine_token,
    verify_machine_token_hash,
)


class TestMachineToken:
    def test_generate_has_prefix(self) -> None:
        token = generate_machine_token()
        assert token.startswith("mch_")
        assert len(token) > 12  # prefix + at least some random bytes

    def test_hash_returns_hash_and_hint(self) -> None:
        token = generate_machine_token()
        hashed, hint = hash_machine_token(token)
        assert hint == token[:12]
        assert hashed.startswith("$argon2")

    def test_verify_correct_token(self) -> None:
        token = generate_machine_token()
        hashed, _ = hash_machine_token(token)
        assert verify_machine_token_hash(token, hashed) is True

    def test_verify_wrong_token(self) -> None:
        token = generate_machine_token()
        hashed, _ = hash_machine_token(token)
        wrong = generate_machine_token()
        assert verify_machine_token_hash(wrong, hashed) is False

    def test_hint_is_first_12_chars(self) -> None:
        token = generate_machine_token()
        _, hint = hash_machine_token(token)
        # "mch_" is 4 chars, hint should be first 12 (prefix + 8)
        assert len(hint) == 12
        assert hint.startswith("mch_")
