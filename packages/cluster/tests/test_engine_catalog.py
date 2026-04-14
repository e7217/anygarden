"""Tests for the engine model catalog."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Base, User
from doorae.engines import (
    ENGINE_CATALOG,
    get_engine_entry,
    is_valid_model,
    is_valid_reasoning_effort,
)


# ── Catalog unit tests ───────────────────────────────────────────────


class TestCatalog:
    def test_known_engines_are_present(self) -> None:
        assert "codex" in ENGINE_CATALOG
        assert "claude-code" in ENGINE_CATALOG
        assert "gemini-cli" in ENGINE_CATALOG
        assert "openai" in ENGINE_CATALOG
        assert "anthropic" in ENGINE_CATALOG

    def test_default_model_is_listed_in_models(self) -> None:
        """Every engine's default_model must appear in its models list."""
        for entry in ENGINE_CATALOG.values():
            model_ids = [m.id for m in entry.models]
            assert entry.default_model in model_ids, (
                f"{entry.engine}: default {entry.default_model} missing from {model_ids}"
            )

    def test_get_engine_entry_unknown(self) -> None:
        assert get_engine_entry("nonexistent") is None

    def test_is_valid_model(self) -> None:
        assert is_valid_model("codex", "gpt-5.4") is True
        assert is_valid_model("codex", "nonexistent") is False
        assert is_valid_model("unknown-engine", "gpt-5.4") is False

    def test_is_valid_reasoning_effort_engine_level(self) -> None:
        """Without specifying a model, engine-level levels apply."""
        assert is_valid_reasoning_effort("codex", "medium") is True
        assert is_valid_reasoning_effort("codex", "xhigh") is False  # engine-level doesn't include xhigh

    def test_is_valid_reasoning_effort_model_level(self) -> None:
        """Per-model reasoning_levels narrow the engine-level list."""
        # gpt-5.4 supports xhigh at model level
        assert is_valid_reasoning_effort("codex", "xhigh", model="gpt-5.4") is True
        # gpt-5.4-mini does NOT support xhigh
        assert is_valid_reasoning_effort("codex", "xhigh", model="gpt-5.4-mini") is False

    def test_is_valid_reasoning_effort_unknown_engine(self) -> None:
        assert is_valid_reasoning_effort("unknown", "medium") is False


# ── API endpoint tests ───────────────────────────────────────────────


@pytest_asyncio.fixture()
async def catalog_env():
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        db.add(admin)
        await db.flush()
        await db.commit()

        token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret
        )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield {"client": client, "token": token}
    finally:
        await client.aclose()
        await engine.dispose()


class TestEngineModelsEndpoint:
    @pytest.mark.asyncio
    async def test_get_codex_models(self, catalog_env) -> None:
        client = catalog_env["client"]
        token = catalog_env["token"]

        resp = await client.get(
            "/api/v1/agents/engines/codex/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] == "codex"
        assert data["default_model"] == "gpt-5.4"
        model_ids = [m["id"] for m in data["models"]]
        assert "gpt-5.4" in model_ids
        assert "gpt-5.4-mini" in model_ids

    @pytest.mark.asyncio
    async def test_get_unknown_engine_returns_404(self, catalog_env) -> None:
        client = catalog_env["client"]
        token = catalog_env["token"]

        resp = await client.get(
            "/api/v1/agents/engines/no-such-engine/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_requires_admin(self, catalog_env) -> None:
        """Anonymous requests should be rejected."""
        client = catalog_env["client"]

        resp = await client.get("/api/v1/agents/engines/codex/models")
        # Without any auth header, expect 401 or 403
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_per_model_reasoning_levels(self, catalog_env) -> None:
        """Models that narrow reasoning_levels should surface them."""
        client = catalog_env["client"]
        token = catalog_env["token"]

        resp = await client.get(
            "/api/v1/agents/engines/codex/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
        gpt54 = next(m for m in data["models"] if m["id"] == "gpt-5.4")
        assert "xhigh" in gpt54["reasoning_levels"]
        mini = next(m for m in data["models"] if m["id"] == "gpt-5.4-mini")
        assert "xhigh" not in mini["reasoning_levels"]
