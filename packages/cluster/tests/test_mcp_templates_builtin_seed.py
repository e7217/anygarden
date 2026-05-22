"""Tests for MCPTemplateService.seed_builtins — idempotency + upsert (#124)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, MCPServerTemplate
from anygarden.mcp_templates import builtin as builtin_mod
from anygarden.mcp_templates.encryption import MCPSecrets
from anygarden.mcp_templates.service import MCPTemplateService


@pytest_asyncio.fixture()
async def service():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    svc = MCPTemplateService(
        factory, secrets=MCPSecrets(Fernet.generate_key()),
    )
    yield {"factory": factory, "service": svc}
    await engine.dispose()


class TestBuiltinSeed:
    @pytest.mark.asyncio
    async def test_seed_inserts_all_builtins_in_fresh_db(self, service):
        await service["service"].seed_builtins()
        async with service["factory"]() as db:
            rows = (await db.execute(
                select(MCPServerTemplate).where(
                    MCPServerTemplate.source == "builtin"
                )
            )).scalars().all()
            names = {r.name for r in rows}
        # Plan §3 specifies five builtins at minimum.
        assert len(names) >= 5
        assert {"github", "slack", "notion", "linear", "filesystem"} <= names

    @pytest.mark.asyncio
    async def test_seed_is_idempotent(self, service):
        # Simulate two cluster boots back-to-back. The second run must
        # not create duplicate rows — name uniqueness would already
        # raise, but we want a clean upsert path.
        await service["service"].seed_builtins()
        await service["service"].seed_builtins()
        async with service["factory"]() as db:
            rows = (await db.execute(
                select(MCPServerTemplate).where(
                    MCPServerTemplate.source == "builtin"
                )
            )).scalars().all()
        # Exactly one row per builtin spec.
        assert len(rows) == len(builtin_mod.BUILTIN_TEMPLATES)

    @pytest.mark.asyncio
    async def test_seed_updates_existing_on_config_drift(self, service):
        """When BUILTIN_TEMPLATES changes between cluster versions,
        the next boot must carry the updated config into the DB row
        (upsert by name). Without this, operators would have to clear
        the table manually on every upgrade."""
        svc = service["service"]
        await svc.seed_builtins()

        async with service["factory"]() as db:
            row = (await db.execute(
                select(MCPServerTemplate).where(
                    MCPServerTemplate.name == "github"
                )
            )).scalar_one()
            # Simulate an old cluster shipping a stale display_name.
            row.display_name = "Old Label"
            await db.commit()

        await svc.seed_builtins()

        async with service["factory"]() as db:
            row = (await db.execute(
                select(MCPServerTemplate).where(
                    MCPServerTemplate.name == "github"
                )
            )).scalar_one()
            assert row.display_name == "GitHub"
            assert row.source == "builtin"

    @pytest.mark.asyncio
    async def test_seed_does_not_touch_custom_rows(self, service):
        """Custom rows are admin-owned data. Seeding must only
        operate on the builtin subset — a custom row named 'github'
        is legal and must not be clobbered by a future builtin with
        the same name (the uniqueness constraint would raise first
        anyway; this is a belt-and-suspenders test)."""
        svc = service["service"]
        async with service["factory"]() as db:
            db.add(MCPServerTemplate(
                name="internal-kb",
                display_name="Internal KB",
                description="Custom",
                icon=None,
                config_per_engine={"claude-code": {"command": "x"}},
                required_env_vars=[],
                supported_engines=["claude-code"],
                source="custom",
                created_by=None,
            ))
            await db.commit()

        await svc.seed_builtins()

        async with service["factory"]() as db:
            row = (await db.execute(
                select(MCPServerTemplate).where(
                    MCPServerTemplate.name == "internal-kb"
                )
            )).scalar_one()
            assert row.source == "custom"
            assert row.display_name == "Internal KB"
