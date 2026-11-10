"""The test-only route cache in ``conftest`` keeps apps independent (#1001)."""

from __future__ import annotations

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.db.engine import build_session_factory
from anygarden.dependencies import get_admin_identity


async def _status(app) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return (await client.get("/api/v1/usage")).status_code


async def test_dependency_overrides_stay_on_their_own_app(config, engine) -> None:
    first = create_app(config)
    second = create_app(config)
    for app in (first, second):
        app.state.session_factory = build_session_factory(engine)
    assert first.router.routes is not second.router.routes

    def teapot():
        raise HTTPException(status_code=418)

    second.dependency_overrides[get_admin_identity] = teapot
    assert await _status(second) == 418
    assert await _status(first) == 401
    first.dependency_overrides[get_admin_identity] = teapot
    assert await _status(first) == 418


async def test_each_app_gets_the_same_routes(config) -> None:
    first = create_app(config)
    second = create_app(config)

    def paths(app):
        return [(type(r).__name__, getattr(r, "path", None)) for r in app.router.routes]

    assert paths(first) == paths(second)
    assert first.openapi()["paths"].keys() == second.openapi()["paths"].keys()
