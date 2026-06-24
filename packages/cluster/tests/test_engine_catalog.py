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
        assert "codex" in ENGINE_CATALOG
        assert "claude-code" in ENGINE_CATALOG
        assert "gemini-cli" in ENGINE_CATALOG

    def test_default_model_is_listed_in_models(self) -> None:
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
        # Codex CLI validator also accepts ``none``, but the catalog
        # omits it so we don't surface a "disabled" pseudo-level.
        assert is_valid_reasoning_effort("codex", "none") is False

    def test_is_valid_reasoning_effort_model_level(self) -> None:
        """Per-model reasoning_levels narrow the engine-level list."""
        # gpt-5.4 supports xhigh at model level
        assert is_valid_reasoning_effort("codex", "xhigh", model="gpt-5.4") is True
        # gpt-5.2 does NOT support xhigh (only low/medium/high)
        assert is_valid_reasoning_effort("codex", "xhigh", model="gpt-5.2") is False

    def test_is_valid_reasoning_effort_unknown_engine(self) -> None:
        assert is_valid_reasoning_effort("unknown", "medium") is False


# ── API endpoint tests ───────────────────────────────────────────────


# ── Phase 6 deprecation infrastructure ───────────────────────────────


class TestDeprecationFields:
    """Issue #355 Phase 6 — catalog can flag legacy engines.

    Issue #382 flips ``claude-code`` after the Anthropic Agent SDK
    credit split made non-interactive CLI orchestration a cost risk.
    Issue #502 flips ``codex`` (SDK) toward the codex-cli (exec) engine
    after the SDK version-coupling outage. Other engines remain
    non-deprecated until a separate decision explicitly moves them.
    """

    # Engines flagged legacy in the catalog (#382 claude-code, #502 codex).
    DEPRECATED_ENGINES = {"claude-code", "codex"}

    def test_deprecated_flag_matches_expected_engines(self) -> None:
        for name, entry in ENGINE_CATALOG.items():
            assert entry.deprecated is (name in self.DEPRECATED_ENGINES)

    def test_deprecation_note_present_for_deprecated(self) -> None:
        for name, entry in ENGINE_CATALOG.items():
            if name == "claude-code":
                assert entry.deprecation_note is not None
                assert "OpenHands" in entry.deprecation_note
            elif name == "codex":
                assert entry.deprecation_note is not None
                assert "codex-cli" in entry.deprecation_note
            else:
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
        assert data["default_model"] == "gpt-5.5"
        model_ids = [m["id"] for m in data["models"]]
        assert "gpt-5.5" in model_ids
        assert "gpt-5.4" in model_ids
        assert "gpt-5.4-mini" in model_ids
        # #502 — codex (SDK) deprecated toward codex-cli (exec).
        assert data["deprecated"] is True
        assert "codex-cli" in data["deprecation_note"]

    @pytest.mark.asyncio
    async def test_get_claude_code_models_exposes_deprecation_metadata(
        self, catalog_env
    ) -> None:
        client = catalog_env["client"]
        token = catalog_env["token"]

        resp = await client.get(
            "/api/v1/agents/engines/claude-code/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["engine"] == "claude-code"
        assert data["deprecated"] is True
        assert "OpenHands" in data["deprecation_note"]

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
        gpt52 = next(m for m in data["models"] if m["id"] == "gpt-5.2")
        assert "xhigh" not in gpt52["reasoning_levels"]


class TestAvailableEnginesEndpoint:
    @pytest.mark.asyncio
    async def test_available_engines_expose_deprecation_metadata(
        self, catalog_env
    ) -> None:
        factory = catalog_env["client"]._transport.app.state.session_factory
        async with factory() as db:
            machine = Machine(
                name="worker",
                hostname="worker.local",
                owner_user_id=catalog_env["admin_id"],
                status="online",
            )
            db.add(machine)
            await db.flush()
            db.add_all(
                [
                    MachineEngine(machine_id=machine.id, engine="claude-code"),
                    MachineEngine(machine_id=machine.id, engine="codex"),
                ]
            )
            await db.commit()

        resp = await catalog_env["client"].get(
            "/api/v1/agents/engines/available",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        assert resp.status_code == 200
        rows = {row["engine"]: row for row in resp.json()}
        assert rows["claude-code"]["deprecated"] is True
        assert "OpenHands" in rows["claude-code"]["deprecation_note"]
        # #502 — codex (SDK) is now deprecated toward codex-cli (exec).
        assert rows["codex"]["deprecated"] is True
        assert "codex-cli" in rows["codex"]["deprecation_note"]


# ── Issue #359 — gateway model merge ────────────────────────────────


class TestOpenHandsGatewayMerge:
    """``get_engine_models("openhands")`` surfaces ``llm_gateway_models``
    rows alongside the static catalog.

    User-visible regression: pre-#359 the operator could register
    ``qwen3.6:27b`` in the gateway DB but the agent-creation
    dropdown still only showed the 14 static catalog entries
    (Anthropic / OpenAI / Google API-key models). Without an API
    key the user couldn't actually use any of those, so the
    dropdown was effectively empty for ollama-only deployments.
    """

    async def _seed_gateway_model(
        self, factory, *, provider: str, model_name: str, enabled: bool = True
    ) -> None:
        from anygarden.db.models import LLMGatewayModel
        async with factory() as db:
            db.add(
                LLMGatewayModel(
                    model_name=model_name,
                    provider=provider,
                    upstream_model=f"{provider}/{model_name}",
                    api_key_ref="DUMMY_KEY_REF",
                    enabled=enabled,
                )
            )
            await db.commit()

    @pytest.mark.asyncio
    async def test_ollama_model_appears_with_gateway_source(
        self, catalog_env
    ) -> None:
        await self._seed_gateway_model(
            catalog_env["client"]._transport.app.state.session_factory,
            provider="ollama",
            model_name="qwen3.6:27b",
        )
        resp = await catalog_env["client"].get(
            "/api/v1/agents/engines/openhands/models",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        gw = [m for m in data["models"] if m["source"] == "gateway"]
        assert len(gw) == 1
        assert gw[0]["id"] == "openai/qwen3.6:27b"
        assert "qwen3.6:27b" in gw[0]["label"]

    @pytest.mark.asyncio
    async def test_static_catalog_still_present(self, catalog_env) -> None:
        """Gateway merge must not replace the static entries."""
        await self._seed_gateway_model(
            catalog_env["client"]._transport.app.state.session_factory,
            provider="ollama",
            model_name="qwen3.6:27b",
        )
        resp = await catalog_env["client"].get(
            "/api/v1/agents/engines/openhands/models",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        data = resp.json()
        builtin_ids = [m["id"] for m in data["models"] if m["source"] == "builtin"]
        # Phase 4 catalog ships 14 static models; just check >0 to
        # avoid coupling to the exact count.
        assert len(builtin_ids) > 0
        assert "anthropic/claude-opus-4-7" in builtin_ids

    @pytest.mark.asyncio
    async def test_disabled_gateway_rows_skipped(self, catalog_env) -> None:
        await self._seed_gateway_model(
            catalog_env["client"]._transport.app.state.session_factory,
            provider="ollama",
            model_name="paused-model",
            enabled=False,
        )
        resp = await catalog_env["client"].get(
            "/api/v1/agents/engines/openhands/models",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        data = resp.json()
        assert not any(m["id"] == "openai/paused-model" for m in data["models"])

    @pytest.mark.parametrize(
        "engine_name", ["claude-code", "codex", "gemini-cli"]
    )
    @pytest.mark.asyncio
    async def test_other_engines_do_not_get_gateway_merge(
        self, catalog_env, engine_name: str
    ) -> None:
        """Phase 0/1's narrow scope: gateway models surface only on
        the openhands endpoint. Surfacing them on claude-code etc.
        would advertise a route the agent can't currently use
        (engine_secrets stays empty for those engines)."""
        await self._seed_gateway_model(
            catalog_env["client"]._transport.app.state.session_factory,
            provider="ollama",
            model_name="qwen3.6:27b",
        )
        resp = await catalog_env["client"].get(
            f"/api/v1/agents/engines/{engine_name}/models",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        data = resp.json()
        assert not any(m["source"] == "gateway" for m in data["models"])

    @pytest.mark.asyncio
    async def test_no_gateway_rows_returns_only_builtin(
        self, catalog_env
    ) -> None:
        """Empty gateway DB → response is identical to pre-#359."""
        resp = await catalog_env["client"].get(
            "/api/v1/agents/engines/openhands/models",
            headers={"Authorization": f"Bearer {catalog_env['token']}"},
        )
        data = resp.json()
        assert all(m["source"] == "builtin" for m in data["models"])
