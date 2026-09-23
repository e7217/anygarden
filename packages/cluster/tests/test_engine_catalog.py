"""Tests for the engine model catalog."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Machine, MachineEngine, User
from anygarden.engines import (
    ENGINE_CATALOG,
    get_engine_entry,
    is_valid_model,
    is_valid_reasoning_effort,
)
from anygarden.engines.catalog import is_deprecated


# ── Catalog unit tests ───────────────────────────────────────────────


class TestCatalog:
    def test_known_engines_are_present(self) -> None:
        assert "pi-cli" in ENGINE_CATALOG
        assert "codex-cli" in ENGINE_CATALOG
        assert "claude-code" not in ENGINE_CATALOG
        assert "gemini-cli" not in ENGINE_CATALOG

    def test_default_model_is_listed_in_models(self) -> None:
        for entry in ENGINE_CATALOG.values():
            model_ids = [m.id for m in entry.models]
            if entry.engine == "pi-cli":
                assert entry.default_model == ""  # provider-specific; never guess
                assert model_ids
                continue
            assert entry.default_model in model_ids, (
                f"{entry.engine}: default {entry.default_model} missing from {model_ids}"
            )

    def test_get_engine_entry_unknown(self) -> None:
        assert get_engine_entry("nonexistent") is None

    def test_is_valid_model(self) -> None:
        assert is_valid_model("codex-cli", "gpt-5.4") is True
        assert is_valid_model("codex-cli", "nonexistent") is False
        assert is_valid_model("unknown-engine", "gpt-5.4") is False

    def test_is_valid_reasoning_effort_engine_level(self) -> None:
        """Without specifying a model, engine-level levels apply."""
        assert is_valid_reasoning_effort("codex-cli", "medium") is True
        # Codex CLI validator also accepts ``none``, but the catalog
        # omits it so we don't surface a "disabled" pseudo-level.
        assert is_valid_reasoning_effort("codex-cli", "none") is False

    def test_is_valid_reasoning_effort_model_level(self) -> None:
        """Per-model reasoning_levels narrow the engine-level list."""
        # gpt-5.4 supports xhigh at model level
        assert is_valid_reasoning_effort("codex-cli", "xhigh", model="gpt-5.4") is True
        # gpt-5.2 does NOT support xhigh (only low/medium/high)
        assert is_valid_reasoning_effort("codex-cli", "xhigh", model="gpt-5.2") is False
        # GPT-5.6 tiers add the new ``max`` level; older models lack it.
        assert is_valid_reasoning_effort("codex-cli", "max", model="gpt-5.6-sol") is True
        assert is_valid_reasoning_effort("codex-cli", "max", model="gpt-5.4") is False

    def test_is_valid_reasoning_effort_unknown_engine(self) -> None:
        assert is_valid_reasoning_effort("unknown", "medium") is False


# ── API endpoint tests ───────────────────────────────────────────────


# ── Phase 6 deprecation infrastructure ───────────────────────────────


class TestDeprecationFields:
    """Deprecation metadata remains available for future catalog entries."""

    DEPRECATED_ENGINES = set()

    def test_deprecated_flag_matches_expected_engines(self) -> None:
        for name, entry in ENGINE_CATALOG.items():
            assert entry.deprecated is (name in self.DEPRECATED_ENGINES)

    def test_deprecation_note_present_for_deprecated(self) -> None:
        for entry in ENGINE_CATALOG.values():
            assert entry.deprecation_note is None

    def test_is_deprecated_helper(self) -> None:
        # Unknown engine — caller-friendly False.
        assert is_deprecated("no-such-engine") is False
        for name in ENGINE_CATALOG:
            assert is_deprecated(name) is (name in self.DEPRECATED_ENGINES)

    def test_codex_cli_not_deprecated(self) -> None:
        # #502 — the exec engine is the recommended replacement, not legacy.
        assert is_deprecated("codex-cli") is False

    def test_entry_can_carry_deprecation_metadata(self) -> None:
        """Frozen dataclass accepts the new fields when constructed.

        Belt-and-suspenders: if a future EngineCatalogEntry change
        accidentally drops the deprecation fields, this test fails
        loudly rather than silently no-op'ing.
        """
        from anygarden.engines.catalog import EngineCatalogEntry

        entry = EngineCatalogEntry(
            engine="probe",
            default_model="m",
            models=(),
            reasoning_levels=(),
            deprecated=True,
            deprecation_note="probe note",
        )
        assert entry.deprecated is True
        assert entry.deprecation_note == "probe note"


@pytest_asyncio.fixture()
async def catalog_env():
    config = AnygardenSettings(
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
        admin_id = admin.id

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield {"client": client, "token": token, "admin_id": admin_id}
    finally:
        await client.aclose()
        await engine.dispose()


class TestEngineModelsEndpoint:
    @pytest.mark.asyncio
    async def test_get_codex_cli_models(self, catalog_env) -> None:
        client = catalog_env["client"]
        token = catalog_env["token"]

        resp = await client.get(
            "/api/v1/agents/engines/codex-cli/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] == "codex-cli"
        assert data["default_model"] == "gpt-5.6-terra"
        model_ids = [m["id"] for m in data["models"]]
        assert "gpt-5.6-sol" in model_ids
        assert "gpt-5.6-terra" in model_ids
        assert "gpt-5.6-luna" in model_ids
        assert "gpt-5.5" in model_ids
        assert "gpt-5.4" in model_ids
        assert "gpt-5.4-mini" in model_ids
        # #506 — codex-cli is the recommended (non-deprecated) engine.
        assert data["deprecated"] is False
        assert data["deprecation_note"] is None
        assert data["supported_versions"] == ["0.154.0", "0.155.1"]

    @pytest.mark.asyncio
    async def test_pi_cli_exposes_supported_versions(self, catalog_env) -> None:
        resp = await catalog_env["client"].get(
            "/api/v1/agents/engines/pi-cli/models",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        assert resp.status_code == 200
        assert resp.json()["supported_versions"] == ["0.85.1"]

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

        resp = await client.get("/api/v1/agents/engines/codex-cli/models")
        # Without any auth header, expect 401 or 403
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_per_model_reasoning_levels(self, catalog_env) -> None:
        """Models that narrow reasoning_levels should surface them."""
        client = catalog_env["client"]
        token = catalog_env["token"]

        resp = await client.get(
            "/api/v1/agents/engines/codex-cli/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = resp.json()
        gpt54 = next(m for m in data["models"] if m["id"] == "gpt-5.4")
        assert "xhigh" in gpt54["reasoning_levels"]
        assert "max" not in gpt54["reasoning_levels"]
        gpt52 = next(m for m in data["models"] if m["id"] == "gpt-5.2")
        assert "xhigh" not in gpt52["reasoning_levels"]
        # GPT-5.6 tiers surface the new ``max`` level.
        sol = next(m for m in data["models"] if m["id"] == "gpt-5.6-sol")
        assert "max" in sol["reasoning_levels"]


def test_supported_versions_match_agent_adapters() -> None:
    """#687 — the catalog mirrors the adapters' exact version gates."""
    pi = pytest.importorskip("anygarden_agent.runtime.execution.pi")
    codex = pytest.importorskip("anygarden_agent.runtime.execution.codex")
    assert ENGINE_CATALOG["pi-cli"].supported_versions == pi.SUPPORTED_VERSIONS
    assert ENGINE_CATALOG["codex-cli"].supported_versions == codex.SUPPORTED_VERSIONS
