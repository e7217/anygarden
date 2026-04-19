"""Integration tests for MCP overlay in lifecycle._build_sync_frame (#124)."""

from __future__ import annotations

import json
import tomllib

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent, AgentFile, Base,
    MCPServerInstance, MCPServerTemplate,
)
from doorae.mcp_templates.encryption import MCPSecrets
from doorae.mcp_templates.service import MCPTemplateService
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    secrets = MCPSecrets(Fernet.generate_key())
    service = MCPTemplateService(factory, secrets=secrets)
    yield {"factory": factory, "service": service, "secrets": secrets}
    await engine.dispose()


async def _build_frame(factory, service, agent_id: str) -> dict:
    bus = MachineBus()
    lifecycle = AgentLifecycle(
        db_factory=factory, machine_bus=bus,
        mcp_template_service=service,
    )
    async with factory() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one()
        return await lifecycle._build_sync_frame(db, agent, rooms=[])


class TestLifecycleOverlay:
    @pytest.mark.asyncio
    async def test_no_instances_yields_no_overlay_file(self, env):
        """Regression guard: an agent with zero MCP instances must
        produce the exact same frame.files it did pre-#124. The skill
        library tests already cover the no-skill case; this one
        specifically checks the MCP path doesn't silently inject an
        empty settings.json."""
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a1",
                desired_state="idle", actual_state="idle",
            )
            db.add(agent)
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        assert frame["files"] == {}

    @pytest.mark.asyncio
    async def test_attached_instance_renders_into_claude_settings(self, env):
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a1",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="github",
                display_name="GitHub",
                description=None,
                icon=None,
                config_per_engine={
                    "claude-code": {
                        "command": "npx",
                        "args": ["-y", "gh"],
                        "env": {"T": "${T}"},
                    },
                },
                required_env_vars=["T"],
                supported_engines=["claude-code"],
                source="custom",
            )
            db.add_all([agent, template])
            await db.flush()
            db.add(MCPServerInstance(
                agent_id=agent.id,
                template_id=template.id,
                env_values_encrypted=env["secrets"].encrypt_dict({"T": "secret"}),
                enabled=True,
            ))
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        # Issue #142 — Claude Code 2.x reads project-local MCP config
        # from ``.mcp.json`` at the workspace root, not from
        # ``.claude/settings.json``'s mcpServers section (which 2.x
        # silently ignores).
        assert ".mcp.json" in frame["files"]
        data = json.loads(frame["files"][".mcp.json"])
        # Credential gets rendered as plaintext in the settings file —
        # that's intentional: the DB has the ciphertext, the on-disk
        # manifest (which the machine materialises inside the agent's
        # own cwd) has the value the engine can use.
        assert data["mcpServers"]["github"]["env"]["T"] == "secret"

    @pytest.mark.asyncio
    async def test_disabled_instance_is_skipped(self, env):
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a1",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="github",
                display_name="GitHub",
                config_per_engine={
                    "claude-code": {"command": "npx"},
                },
                required_env_vars=[],
                supported_engines=["claude-code"],
                source="custom",
            )
            db.add_all([agent, template])
            await db.flush()
            db.add(MCPServerInstance(
                agent_id=agent.id, template_id=template.id,
                enabled=False,
            ))
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        # Disabled → overlay skipped → no .mcp.json produced.
        assert ".mcp.json" not in frame["files"]

    @pytest.mark.asyncio
    async def test_admin_agent_file_is_merged_with_overlay(self, env):
        """Admin-authored AgentFile settings.json + MCP overlay should
        combine: admin's mcpServers keys win on collision, other
        overlays fill in, non-mcpServers keys are preserved."""
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a1",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="slack",
                display_name="Slack",
                config_per_engine={
                    "claude-code": {"command": "overlay"},
                },
                required_env_vars=[],
                supported_engines=["claude-code"],
                source="custom",
            )
            db.add_all([agent, template])
            await db.flush()
            # Issue #142 — admin overrides for MCP live in ``.mcp.json``
            # at the workspace root (same file Claude Code 2.x reads).
            # Non-MCP admin settings like ``permissions.allow`` stay
            # in ``.claude/settings.json`` and don't participate in the
            # MCP merge path; this test focuses on the MCP merge so we
            # keep the admin file in the new registry location.
            db.add(AgentFile(
                agent_id=agent.id,
                path=".mcp.json",
                content=json.dumps({
                    "custom_key": "kept",
                    "mcpServers": {"github": {"command": "admin-gh"}},
                }),
            ))
            db.add(MCPServerInstance(
                agent_id=agent.id, template_id=template.id,
            ))
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        data = json.loads(frame["files"][".mcp.json"])
        # Admin's non-mcp keys preserved verbatim.
        assert data["custom_key"] == "kept"
        # Admin's github entry preserved (admin wins on collision).
        assert data["mcpServers"]["github"] == {"command": "admin-gh"}
        # Overlay's slack entry added on top.
        assert data["mcpServers"]["slack"] == {"command": "overlay"}

    @pytest.mark.asyncio
    async def test_codex_agent_gets_toml_overlay(self, env):
        async with env["factory"]() as db:
            agent = Agent(
                engine="codex", name="c1",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="github",
                display_name="GitHub",
                config_per_engine={
                    "codex": {
                        "command": "npx", "args": ["-y", "gh"],
                        "env": {"T": "v"},
                    },
                },
                required_env_vars=[],
                supported_engines=["codex"],
                source="custom",
            )
            db.add_all([agent, template])
            await db.flush()
            db.add(MCPServerInstance(
                agent_id=agent.id, template_id=template.id,
            ))
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        assert ".codex/config.toml" in frame["files"]
        parsed = tomllib.loads(frame["files"][".codex/config.toml"])
        assert parsed["mcp_servers"]["github"]["command"] == "npx"

    @pytest.mark.asyncio
    async def test_non_mcp_engine_gets_no_overlay(self, env):
        """Agents on engines without MCP support (echo / openai /
        anthropic) must not get a spurious settings file. The agent
        still boots; the overlay simply doesn't apply."""
        async with env["factory"]() as db:
            agent = Agent(
                engine="openai", name="o1",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="github",
                display_name="GitHub",
                config_per_engine={
                    "claude-code": {"command": "x"},
                },
                required_env_vars=[],
                supported_engines=["claude-code"],
                source="custom",
            )
            db.add_all([agent, template])
            await db.flush()
            db.add(MCPServerInstance(
                agent_id=agent.id, template_id=template.id,
            ))
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        # Engine has no MCP settings path — ``_build_sync_frame``
        # must skip rather than crash or render into a bogus path.
        assert frame["files"] == {}
