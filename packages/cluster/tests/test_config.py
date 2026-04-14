"""Tests for DooraeSettings configuration."""

from __future__ import annotations

import os
import secrets

import pytest

from doorae.config import DooraeSettings


class TestConfigDefaults:
    """Verify that sensible defaults are set."""

    def test_default_host(self) -> None:
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.host == "127.0.0.1"

    def test_default_port(self) -> None:
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.port == 8000

    def test_default_log_level(self) -> None:
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.log_level == "INFO"

    def test_default_jwt_expire_hours(self) -> None:
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.jwt_expire_hours == 24

    def test_default_db_url_contains_sqlite(self) -> None:
        cfg = DooraeSettings(jwt_secret="test")
        assert "sqlite" in cfg.db_url


class TestConfigEnvOverride:
    """Verify that DOORAE_* environment variables override defaults."""

    def test_env_override_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOORAE_HOST", "0.0.0.0")
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.host == "0.0.0.0"

    def test_env_override_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOORAE_PORT", "9999")
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.port == 9999

    def test_env_override_db_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOORAE_DB_URL", "sqlite+aiosqlite:///custom.db")
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.db_url == "sqlite+aiosqlite:///custom.db"

    def test_env_override_jwt_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = secrets.token_urlsafe(32)
        monkeypatch.setenv("DOORAE_JWT_SECRET", secret)
        cfg = DooraeSettings()
        assert cfg.jwt_secret == secret

    def test_env_override_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOORAE_LOG_LEVEL", "DEBUG")
        cfg = DooraeSettings(jwt_secret="test")
        assert cfg.log_level == "DEBUG"


class TestReachableHost:
    """``reachable_host()`` picks a host a client can actually dial."""

    def test_preserves_routable_host(self) -> None:
        cfg = DooraeSettings(jwt_secret="test", host="192.168.100.81")
        assert cfg.reachable_host() == "192.168.100.81"

    def test_preserves_loopback(self) -> None:
        cfg = DooraeSettings(jwt_secret="test", host="127.0.0.1")
        assert cfg.reachable_host() == "127.0.0.1"

    def test_rewrites_wildcard_ipv4(self) -> None:
        cfg = DooraeSettings(jwt_secret="test", host="0.0.0.0")
        assert cfg.reachable_host() == "127.0.0.1"

    def test_rewrites_wildcard_ipv6(self) -> None:
        cfg = DooraeSettings(jwt_secret="test", host="::")
        assert cfg.reachable_host() == "127.0.0.1"

    def test_rewrites_empty(self) -> None:
        cfg = DooraeSettings(jwt_secret="test", host="")
        assert cfg.reachable_host() == "127.0.0.1"
