"""Tests for version_service — local version, PyPI fetch, comparison (#546)."""

from __future__ import annotations

import httpx
import pytest

from anygarden.system import version_service


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ── get_local_version ─────────────────────────────────────────────────


def test_get_local_version_returns_installed() -> None:
    import anygarden

    assert version_service.get_local_version("anygarden") == anygarden.__version__


def test_get_local_version_missing_package_falls_back(monkeypatch) -> None:
    from importlib.metadata import PackageNotFoundError

    def _raise(_name):
        raise PackageNotFoundError

    monkeypatch.setattr(version_service, "_dist_version", _raise)
    assert version_service.get_local_version("nonexistent-pkg") == "0.0.0+dev"


# ── fetch_pypi_latest ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_pypi_latest_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "anygarden" in str(request.url)
        return httpx.Response(200, json={"info": {"version": "0.16.0"}})

    async with _client(handler) as client:
        assert await version_service.fetch_pypi_latest("anygarden", client=client) == "0.16.0"


@pytest.mark.asyncio
async def test_fetch_pypi_latest_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(handler) as client:
        assert await version_service.fetch_pypi_latest("nope", client=client) is None


@pytest.mark.asyncio
async def test_fetch_pypi_latest_network_error_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        assert await version_service.fetch_pypi_latest("anygarden", client=client) is None


@pytest.mark.asyncio
async def test_fetch_pypi_latest_malformed_json_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async with _client(handler) as client:
        assert await version_service.fetch_pypi_latest("anygarden", client=client) is None


# ── is_update_available ───────────────────────────────────────────────


def test_update_available_when_newer() -> None:
    assert version_service.is_update_available("0.15.0", "0.16.0") is True


def test_no_update_when_equal() -> None:
    assert version_service.is_update_available("0.15.0", "0.15.0") is False


def test_no_update_when_latest_older() -> None:
    assert version_service.is_update_available("0.16.0", "0.15.0") is False


def test_no_update_when_latest_none() -> None:
    assert version_service.is_update_available("0.15.0", None) is False


def test_semver_compared_numerically_not_lexically() -> None:
    # Lexical compare would rank "0.9.0" > "0.10.0"; PEP 440 must not.
    assert version_service.is_update_available("0.9.0", "0.10.0") is True


def test_dev_checkout_suppresses_update() -> None:
    # A source checkout ("0.0.0+dev") can't be meaningfully compared to a
    # release, so no update badge.
    assert version_service.is_update_available("0.0.0+dev", "0.16.0") is False
