"""Tests for the engine install-channel abstraction (#553)."""

from __future__ import annotations

import httpx
import pytest
from anygarden_machine.engines.channels import NpmGlobal, PipVenv


class TestNpmNormalize:
    def test_strips_name_prefix(self):
        # claude ships `claude <ver>` from `--version`.
        assert NpmGlobal().normalize("claude 2.1.211") == "2.1.211"

    def test_bare_version(self):
        # codex prints a bare version.
        assert NpmGlobal().normalize("0.144.1") == "0.144.1"

    def test_prerelease(self):
        assert NpmGlobal().normalize("1.2.3-preview.4") == "1.2.3-preview.4"

    def test_trailing_build_info(self):
        assert NpmGlobal().normalize("gemini 0.39.1 (build abc)") == "0.39.1"

    def test_no_version(self):
        assert NpmGlobal().normalize("unknown") is None

    def test_empty(self):
        assert NpmGlobal().normalize("") is None


class TestNpmUpdateArgv:
    def test_latest(self):
        assert NpmGlobal().update_argv("@openai/codex") == [
            "npm",
            "install",
            "-g",
            "@openai/codex@latest",
        ]

    def test_python_arg_ignored(self):
        # system-global channel ignores an interpreter path.
        assert NpmGlobal().update_argv("@google/gemini-cli", python="/x/py") == [
            "npm",
            "install",
            "-g",
            "@google/gemini-cli@latest",
        ]


class TestPipNormalize:
    def test_clean(self):
        assert PipVenv().normalize("1.2.3") == "1.2.3"

    def test_local_dev_segment_preserved(self):
        assert PipVenv().normalize("0.0.0+dev") == "0.0.0+dev"

    def test_no_version(self):
        assert PipVenv().normalize("n/a") is None


class TestPipUpdateArgv:
    def test_with_python(self):
        assert PipVenv().update_argv("openhands-sdk", python="/venv/bin/python") == [
            "/venv/bin/python",
            "-m",
            "pip",
            "install",
            "-U",
            "openhands-sdk",
        ]

    def test_requires_python(self):
        # pip is interpreter-scoped; refuse without a target interpreter.
        with pytest.raises(ValueError):
            PipVenv().update_argv("openhands-sdk")


def _mock_client(handler) -> httpx.AsyncClient:
    """An httpx client whose responses come from ``handler`` (no network)."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestNpmLatestVersion:
    async def test_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "registry.npmjs.org" in str(request.url)
            assert "@openai/codex" in str(request.url)
            return httpx.Response(200, json={"version": "1.5.0"})

        v = await NpmGlobal().latest_version("@openai/codex", client=_mock_client(handler))
        assert v == "1.5.0"

    async def test_404_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        v = await NpmGlobal().latest_version("@x/y", client=_mock_client(handler))
        assert v is None

    async def test_network_error_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        v = await NpmGlobal().latest_version("@x/y", client=_mock_client(handler))
        assert v is None

    async def test_missing_version_field_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        v = await NpmGlobal().latest_version("@x/y", client=_mock_client(handler))
        assert v is None


class TestPipLatestVersion:
    async def test_ok(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert "pypi.org" in str(request.url)
            return httpx.Response(200, json={"info": {"version": "2.3.4"}})

        v = await PipVenv().latest_version("openhands-sdk", client=_mock_client(handler))
        assert v == "2.3.4"

    async def test_server_error_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        v = await PipVenv().latest_version("openhands-sdk", client=_mock_client(handler))
        assert v is None
