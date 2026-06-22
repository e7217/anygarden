"""Regression tests for #473 — asset-like SPA fallback returns 404.

The catch-all ``spa_fallback`` route served ``index.html`` (200,
text/html) for *every* unmatched path, including asset requests like
``/favicon.ico``. Browsers then tried to parse HTML as an icon. Real SPA
routes have no file extension (``/rooms/abc``) and must still resolve to
``index.html``; asset-like paths (a ``.`` in the final segment) that
don't map to a real file must 404.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from anygarden.app import _is_asset_like_path


class TestIsAssetLikePath:
    @pytest.mark.parametrize(
        "path",
        [
            "favicon.ico",
            "robots.txt",
            "manifest.webmanifest",
            "nested/path/logo.png",
            "sitemap.xml",
        ],
    )
    def test_extensioned_paths_are_asset_like(self, path: str) -> None:
        assert _is_asset_like_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "rooms",
            "rooms/abc",
            "projects/123/settings",
            "login",
        ],
    )
    def test_extensionless_paths_are_spa_routes(self, path: str) -> None:
        assert _is_asset_like_path(path) is False

    def test_dot_only_in_non_final_segment_is_spa_route(self) -> None:
        # A dot earlier in the path but not in the final segment must NOT
        # be treated as an asset request — only the basename matters.
        assert _is_asset_like_path("v1.2/rooms") is False


class TestSpaFallbackIntegration:
    """End-to-end check of the catch-all route's HTTP behavior.

    The real ``create_app`` only registers ``spa_fallback`` when a built
    static dir exists (a vite artifact, absent in CI). Rather than
    relocate the whole module (which also drives alembic via ``__file__``),
    we mount a minimal app that wires the *same* decision (``_is_asset_like_path``)
    against a temp static dir — exercising the route contract directly.
    """

    @pytest.fixture()
    def client(self, tmp_path: Path) -> TestClient:
        from fastapi import FastAPI
        from starlette.responses import FileResponse, Response

        static_dir = tmp_path / "static"
        static_dir.mkdir(parents=True)
        index_html = static_dir / "index.html"
        index_html.write_text("<!doctype html><title>SPA</title>")

        app = FastAPI()

        @app.get("/{path:path}")
        async def spa_fallback(path: str):
            file = static_dir / path
            if file.is_file():
                return FileResponse(file)
            if _is_asset_like_path(path):
                return Response(status_code=404)
            return FileResponse(index_html)

        return TestClient(app)

    def test_favicon_returns_404_not_index_html(self, client: TestClient) -> None:
        resp = client.get("/favicon.ico")
        assert resp.status_code == 404

    def test_spa_route_returns_index_html(self, client: TestClient) -> None:
        resp = client.get("/rooms/some-room-id")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "SPA" in resp.text
