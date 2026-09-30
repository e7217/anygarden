"""Retired engines stay readable but cannot reach a runnable manifest."""

from unittest.mock import AsyncMock

import pytest
from .test_agents_api import agents_env as agents_env
from anygarden.app import _mark_removed_engine_agents
from anygarden.db.models import Agent, AgentFile, MachineEngine

RETIRED = ["claude-code", "gemini-cli", "openhands", "claude_code", "gemini_cli"]


@pytest.mark.parametrize("engine_name", RETIRED)
async def test_retired_configuration_preserved_and_execution_blocked(
    agents_env, engine_name
):
    env = agents_env
    headers = {"Authorization": f"Bearer {env['token']}"}
    lifecycle = env["lifecycle"]
    lifecycle._mcp_template_service = AsyncMock()
    async with env["factory"]() as db:
        agent = Agent(
            name="legacy",
            engine=engine_name,
            model="old-model",
            provider="old-provider",
            agents_md="keep role",
            generation=19,
            desired_state="running",
            actual_state="running",
            placed_on_machine_id=env["machine"].id,
        )
        db.add(agent)
        await db.flush()
        original = (
            agent.engine,
            agent.model,
            agent.provider,
            agent.agents_md,
            agent.generation,
            agent.desired_state,
            agent.actual_state,
            agent.placed_on_machine_id,
        )
        db.add(
            AgentFile(
                agent_id=agent.id, path="legacy/config.json", content="preserve me"
            )
        )
        db.add(MachineEngine(machine_id=env["machine"].id, engine=engine_name))
        await db.commit()
        agent_id = agent.id
        await _mark_removed_engine_agents(db)
        await db.commit()
    response = await env["client"].post(
        f"/api/v1/agents/{agent_id}/start", headers=headers
    )
    assert response.status_code == 422 and "preserved" in response.json()["detail"]
    await lifecycle.request_start(agent_id)
    async with env["factory"]() as db:
        agent = await db.get(Agent, agent_id)
        frame = await lifecycle._build_sync_frame(db, agent, ["room"])
        assert frame == dict(
            type="sync_desired_state",
            agent_id=agent_id,
            desired_state="stopped",
            generation=19,
        )
        assert (
            agent.engine,
            agent.model,
            agent.provider,
            agent.agents_md,
            agent.generation,
            agent.desired_state,
            agent.actual_state,
            agent.placed_on_machine_id,
        ) == original
        assert agent.unavailable_code == "engine_removed"
        from sqlalchemy import select

        assert (
            await db.execute(select(AgentFile).where(AgentFile.agent_id == agent_id))
        ).scalar_one().content == "preserve me"
    lifecycle._mcp_template_service.build_for_agent.assert_not_called()
    available = await env["client"].get(
        "/api/v1/agents/engines/available", headers=headers
    )
    assert engine_name not in {row["engine"] for row in available.json()}
    created = await env["client"].post(
        "/api/v1/agents", headers=headers, json=dict(name="new", engine=engine_name)
    )
    assert created.status_code == 422
