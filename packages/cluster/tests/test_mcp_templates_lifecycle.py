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


async def _build_frame(
    factory,
    service,
    agent_id: str,
    *,
    cluster_external_url: str | None = None,
) -> dict:
    bus = MachineBus()
    lifecycle = AgentLifecycle(
        db_factory=factory, machine_bus=bus,
        mcp_template_service=service,
        cluster_external_url=cluster_external_url,
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


class TestDoorAESelfRegistration:
    """Issue #277 — every spawn frame for an MCP-supporting engine
    must carry the doorae self-MCP entry by default, plus a fresh
    bearer token surfaced on ``doorae_mcp_token`` for codex
    process-env injection."""

    @pytest.mark.asyncio
    async def test_claude_code_gets_streamable_http_default(self, env):
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a1",
                desired_state="idle", actual_state="idle",
            )
            db.add(agent)
            await db.commit()
            aid = agent.id

        frame = await _build_frame(
            env["factory"], env["service"], aid,
            cluster_external_url="http://localhost:8001",
        )
        rendered = json.loads(frame["files"][".mcp.json"])
        assert "doorae" in rendered["mcpServers"]
        entry = rendered["mcpServers"]["doorae"]
        assert entry["type"] == "http"
        assert entry["url"] == "http://localhost:8001/mcp/rpc"
        # The header carries a real token, and the same plaintext
        # value rides on doorae_mcp_token for the machine spawner.
        token = frame["doorae_mcp_token"]
        assert token
        assert entry["headers"]["Authorization"] == f"Bearer {token}"

    @pytest.mark.asyncio
    async def test_codex_uses_env_var_indirection(self, env):
        async with env["factory"]() as db:
            agent = Agent(
                engine="codex", name="cx",
                desired_state="idle", actual_state="idle",
            )
            db.add(agent)
            await db.commit()
            aid = agent.id

        frame = await _build_frame(
            env["factory"], env["service"], aid,
            cluster_external_url="http://localhost:8001",
        )
        rendered = tomllib.loads(frame["files"][".codex/config.toml"])
        entry = rendered["mcp_servers"]["doorae"]
        assert entry["url"] == "http://localhost:8001/mcp/rpc"
        assert entry["bearer_token_env_var"] == "DOORAE_AGENT_TOKEN"
        # Plaintext token must NOT leak into the .toml file (the
        # whole point of ``bearer_token_env_var``); the spawn frame
        # ferries it out-of-band on ``doorae_mcp_token`` for the
        # machine spawner to inject as DOORAE_AGENT_TOKEN env.
        token = frame["doorae_mcp_token"]
        assert token
        assert token not in frame["files"][".codex/config.toml"]

    @pytest.mark.asyncio
    async def test_default_skipped_when_cluster_url_unset(self, env):
        """Without a cluster URL the lifecycle must NOT mint a token
        or write a partial doorae entry. (Tests / edge environments
        rely on this being a no-op.)"""
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a2",
                desired_state="idle", actual_state="idle",
            )
            db.add(agent)
            await db.commit()
            aid = agent.id

        frame = await _build_frame(env["factory"], env["service"], aid)
        assert frame["files"] == {}
        assert frame["doorae_mcp_token"] is None

    @pytest.mark.asyncio
    async def test_admin_attachment_overrides_default(self, env):
        """If admin attaches an external MCP under the reserved name
        ``doorae`` it wins on key collision (escape hatch — plan
        §3.2 결정 1)."""
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a3",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="doorae",
                display_name="Doorae (admin override)",
                config_per_engine={
                    "claude-code": {"command": "/bin/false", "args": [], "env": {}},
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

        frame = await _build_frame(
            env["factory"], env["service"], aid,
            cluster_external_url="http://localhost:8001",
        )
        rendered = json.loads(frame["files"][".mcp.json"])
        entry = rendered["mcpServers"]["doorae"]
        # Admin's stdio command shape wins over the builtin http form.
        assert entry.get("command") == "/bin/false"
        assert "type" not in entry

    @pytest.mark.asyncio
    async def test_default_coexists_with_admin_attachment_under_other_name(
        self, env,
    ):
        """The common case: admin attaches an external MCP (e.g.
        ``github``) under a non-conflicting name, and the doorae
        builtin coexists in the same merged manifest."""
        async with env["factory"]() as db:
            agent = Agent(
                engine="claude-code", name="a4",
                desired_state="idle", actual_state="idle",
            )
            template = MCPServerTemplate(
                name="github",
                display_name="GitHub",
                config_per_engine={
                    "claude-code": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "env": {},
                    },
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

        frame = await _build_frame(
            env["factory"], env["service"], aid,
            cluster_external_url="http://localhost:8001",
        )
        rendered = json.loads(frame["files"][".mcp.json"])
        servers = rendered["mcpServers"]
        assert "doorae" in servers
        assert "github" in servers
        assert servers["doorae"]["type"] == "http"
        assert servers["github"]["command"] == "npx"
