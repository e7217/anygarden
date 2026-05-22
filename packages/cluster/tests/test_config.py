"""Tests for AnygardenSettings configuration."""

from __future__ import annotations

import os
import secrets

import pytest

from anygarden.config import AnygardenSettings


class TestConfigDefaults:
    """Verify that sensible defaults are set."""

    def test_default_host(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.host == "127.0.0.1"

    def test_default_port(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.port == 8000

    def test_default_log_level(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.log_level == "INFO"

    def test_default_jwt_expire_hours(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.jwt_expire_hours == 24

    def test_default_db_url_contains_sqlite(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test")
        assert "sqlite" in cfg.db_url


class TestConfigEnvOverride:
    """Verify that ANYGARDEN_* environment variables override defaults."""

    def test_env_override_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANYGARDEN_HOST", "0.0.0.0")
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.host == "0.0.0.0"

    def test_env_override_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANYGARDEN_PORT", "9999")
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.port == 9999

    def test_env_override_db_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANYGARDEN_DB_URL", "sqlite+aiosqlite:///custom.db")
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.db_url == "sqlite+aiosqlite:///custom.db"

    def test_env_override_jwt_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = secrets.token_urlsafe(32)
        monkeypatch.setenv("ANYGARDEN_JWT_SECRET", secret)
        cfg = AnygardenSettings()
        assert cfg.jwt_secret == secret

    def test_env_override_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANYGARDEN_LOG_LEVEL", "DEBUG")
        cfg = AnygardenSettings(jwt_secret="test")
        assert cfg.log_level == "DEBUG"


class TestReachableHost:
    """``reachable_host()`` picks a host a client can actually dial."""

    def test_preserves_routable_host(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="192.168.100.81")
        assert cfg.reachable_host() == "192.168.100.81"

    def test_preserves_loopback(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="127.0.0.1")
        assert cfg.reachable_host() == "127.0.0.1"

    def test_rewrites_wildcard_ipv4(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="0.0.0.0")
        assert cfg.reachable_host() == "127.0.0.1"

    def test_rewrites_wildcard_ipv6(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="::")
        assert cfg.reachable_host() == "127.0.0.1"

    def test_rewrites_empty(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="")
        assert cfg.reachable_host() == "127.0.0.1"


class TestClusterExternalUrl:
    """``cluster_external_url_or_default()`` chooses the URL agents
    use to reach the cluster's ``/mcp/rpc``. (#277)"""

    def test_falls_back_to_reachable_host_and_port(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="127.0.0.1", port=8001)
        assert cfg.cluster_external_url_or_default() == "http://127.0.0.1:8001"

    def test_explicit_override(self) -> None:
        cfg = AnygardenSettings(
            jwt_secret="test",
            cluster_external_url="https://chat.example.com",
        )
        assert (
            cfg.cluster_external_url_or_default() == "https://chat.example.com"
        )

    def test_strips_trailing_slash_from_explicit(self) -> None:
        # The MCP entry concatenates ``/mcp/rpc`` so a trailing slash
        # would produce ``//mcp/rpc``. Trim once at the source.
        cfg = AnygardenSettings(
            jwt_secret="test",
            cluster_external_url="https://chat.example.com/",
        )
        assert (
            cfg.cluster_external_url_or_default() == "https://chat.example.com"
        )

    def test_fallback_uses_reachable_host_when_wildcard(self) -> None:
        cfg = AnygardenSettings(jwt_secret="test", host="0.0.0.0", port=8042)
        # ``0.0.0.0`` rewrites to ``127.0.0.1`` via reachable_host()
        assert cfg.cluster_external_url_or_default() == "http://127.0.0.1:8042"
