"""Tests for the CLI → ANYGARDEN_* environment promotion.

The CLI ``--host`` / ``--port`` flags describe where uvicorn *binds*, not
where clients should dial. Docker port mapping, reverse proxies, and k8s
Services routinely make those two addresses different, which is why
production deployments already rely on ``ANYGARDEN_HOST`` / ``ANYGARDEN_PORT``
to tell agents where to connect.

``_apply_runtime_env`` may fall back to CLI values when the operator
hasn't set those env vars, but it must never clobber a pre-existing
``ANYGARDEN_HOST`` / ``ANYGARDEN_PORT``.
"""

from __future__ import annotations

import os

import pytest

from anygarden.cli import _apply_runtime_env


@pytest.fixture(autouse=True)
def _restore_anygarden_env():
    """Snapshot ANYGARDEN_* env vars and restore after each test.

    ``monkeypatch`` only tracks writes it performed; ``_apply_runtime_env``
    writes into ``os.environ`` directly, so state leaks across tests
    without this explicit restore.
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("ANYGARDEN_")}
    try:
        yield
    finally:
        for k in list(os.environ.keys()):
            if k.startswith("ANYGARDEN_") and k not in saved:
                del os.environ[k]
        for k, v in saved.items():
            os.environ[k] = v


class TestApplyRuntimeEnv:
    def test_sets_host_from_cli_when_env_unset(self) -> None:
        os.environ.pop("ANYGARDEN_HOST", None)
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_HOST"] == "127.0.0.1"

    def test_sets_port_from_cli_when_env_unset(self) -> None:
        os.environ.pop("ANYGARDEN_PORT", None)
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_PORT"] == "8001"

    def test_preserves_operator_host_behind_wildcard_bind(self) -> None:
        """Operator pattern: ANYGARDEN_HOST=public.example.com + --host 0.0.0.0.

        uvicorn must bind every interface, but agents on remote machines
        need to dial ``public.example.com``. Promoting ``0.0.0.0`` into
        ANYGARDEN_HOST silently breaks this whole class of deployments.
        """
        os.environ["ANYGARDEN_HOST"] = "public.example.com"
        _apply_runtime_env("0.0.0.0", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_HOST"] == "public.example.com"

    def test_preserves_operator_port_with_reverse_proxy(self) -> None:
        """Operator pattern: ANYGARDEN_PORT=8443 (reverse proxy front port)
        plus ``--port 8001`` for the internal uvicorn bind. The agent URL
        must still point at 8443.
        """
        os.environ["ANYGARDEN_PORT"] = "8443"
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_PORT"] == "8443"

    def test_db_url_override_is_authoritative(self) -> None:
        """``--db`` is an explicit override, so it should win over any
        pre-existing ``ANYGARDEN_DB_URL``.
        """
        os.environ["ANYGARDEN_DB_URL"] = "sqlite+aiosqlite:///from-env.db"
        _apply_runtime_env(
            "127.0.0.1",
            8001,
            db_url="sqlite+aiosqlite:///from-cli.db",
            log_level="INFO",
        )
        assert os.environ["ANYGARDEN_DB_URL"] == "sqlite+aiosqlite:///from-cli.db"

    def test_db_url_env_preserved_when_cli_omitted(self) -> None:
        os.environ["ANYGARDEN_DB_URL"] = "sqlite+aiosqlite:///from-env.db"
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_DB_URL"] == "sqlite+aiosqlite:///from-env.db"

    def test_empty_port_env_is_treated_as_unset(self) -> None:
        """``ANYGARDEN_PORT=""`` is how docker compose, ``export FOO=``, and
        many CI shells spell "not meaningfully set". Pydantic refuses to
        parse the empty string as an int and the server fails to boot, so
        ``setdefault`` alone isn't enough — fall back to the CLI value.
        """
        os.environ["ANYGARDEN_PORT"] = ""
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_PORT"] == "8001"

    def test_empty_host_env_is_treated_as_unset(self) -> None:
        os.environ["ANYGARDEN_HOST"] = ""
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="INFO")
        assert os.environ["ANYGARDEN_HOST"] == "127.0.0.1"

    def test_empty_log_level_env_is_treated_as_unset(self) -> None:
        os.environ["ANYGARDEN_LOG_LEVEL"] = ""
        _apply_runtime_env("127.0.0.1", 8001, db_url=None, log_level="DEBUG")
        assert os.environ["ANYGARDEN_LOG_LEVEL"] == "DEBUG"
