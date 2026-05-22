"""Unit tests for MCPSecrets — round-trip + failure modes (#124)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from anygarden.mcp_templates.encryption import MCPSecrets, MCPSecretsUnavailable


class TestMCPSecretsRoundTrip:
    def test_encrypt_decrypt_preserves_values(self) -> None:
        key = Fernet.generate_key()
        secrets = MCPSecrets(key)
        payload = {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_example", "OTHER": "42"}
        token = secrets.encrypt_dict(payload)
        assert isinstance(token, bytes)
        assert payload == secrets.decrypt_dict(token)

    def test_decrypt_empty_token_returns_empty_dict(self) -> None:
        # The service uses NULL ciphertext to mean "no credentials
        # required". Reading that through decrypt_dict should just
        # give an empty dict without forcing every caller to guard.
        secrets = MCPSecrets(Fernet.generate_key())
        assert secrets.decrypt_dict(None) == {}
        assert secrets.decrypt_dict(b"") == {}

    def test_decrypt_with_wrong_key_raises_value_error(self) -> None:
        token = MCPSecrets(Fernet.generate_key()).encrypt_dict({"k": "v"})
        other = MCPSecrets(Fernet.generate_key())
        with pytest.raises(ValueError, match="Failed to decrypt"):
            other.decrypt_dict(token)

    def test_str_and_bytes_keys_interchangeable(self) -> None:
        key_bytes = Fernet.generate_key()
        token = MCPSecrets(key_bytes).encrypt_dict({"a": "b"})
        # Same key decoded as str should decrypt the same token.
        key_str = key_bytes.decode("ascii")
        assert MCPSecrets(key_str).decrypt_dict(token) == {"a": "b"}


class TestMCPSecretsInitialization:
    def test_invalid_key_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="not a valid Fernet key"):
            MCPSecrets("not-a-real-key")

    def test_empty_key_in_production_raises(self) -> None:
        with pytest.raises(MCPSecretsUnavailable):
            MCPSecrets.from_config_key("", dev_mode=False)

    def test_empty_key_in_dev_warns_and_generates(self, caplog) -> None:
        import logging

        caplog.set_level(logging.WARNING, logger="anygarden.mcp_templates.encryption")
        secrets = MCPSecrets.from_config_key("", dev_mode=True)
        # Ephemeral key must actually work for round-trip.
        assert secrets.decrypt_dict(secrets.encrypt_dict({"x": "y"})) == {"x": "y"}
        # Loud warning telling the operator this is not for production.
        assert any("ephemeral_key_generated" in r.message for r in caplog.records)

    def test_explicit_key_takes_precedence(self) -> None:
        key = Fernet.generate_key().decode("ascii")
        secrets = MCPSecrets.from_config_key(key, dev_mode=False)
        # If the key wasn't honored this would throw (empty key in
        # prod is a hard error).
        assert secrets.decrypt_dict(secrets.encrypt_dict({"x": "y"})) == {"x": "y"}
