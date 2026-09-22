"""Local DB policy -> daemon -> real spawner argv/stdin -> CLI -> Invocation.

Only process creation and the room runner are replaced; no model is invoked.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from anygarden.db.models import Agent
from anygarden_agent import secrets
from anygarden_agent.cli import agent_main
from anygarden_agent.runtime.execution.contracts import Invocation, SessionScope
from anygarden_agent.runtime.execution.endpoint import CHILD_KEY, CONFIG_KEY, INPUT_KEY
from anygarden_machine.daemon import MachineDaemon
from .test_engine_endpoints import endpoint_env, config
from .test_agents_api import agents_env


async def test_db_to_invocation_preserves_explicit_selection(
    endpoint_env, tmp_path, monkeypatch
):
    e = endpoint_env
    credentials = await e["client"].post(
        e["path"] + "/credentials",
        headers=e["headers"],
        json={"value": "transport-test-key"},
    )
    ref = credentials.json()["id"]
    response = await e["client"].put(
        e["path"], headers=e["headers"], json=config(credential_ref=ref)
    )
    assert response.status_code == 200
    async with e["factory"]() as db:
        agent = await db.get(Agent, e["agent_id"])
        frame = await e["lifecycle"]._build_sync_frame(db, agent, ["room-local"])
    assert frame["provider"] == "local"
    daemon = MachineDaemon(
        server_url="ws://localhost:8000/ws/machines/m",
        machine_id="m",
        machine_token="fixture-token",
        labels={},
        agent_dirs_root=tmp_path / "agents",
        workspace_registry_path=tmp_path / "workspaces.json",
        workspace_signing_key_path=tmp_path / "signing.key",
    )

    async def send(data):
        if data["type"] == "token_request":
            await daemon._handle(
                {
                    "type": "token_grant",
                    "agent_id": e["agent_id"],
                    "agent_token": "fixture-agent-token",
                }
            )

    daemon._send = send
    process = MagicMock()
    process.pid = 42
    process.stderr = None
    process.wait = AsyncMock(return_value=0)
    process.stdin.drain = AsyncMock()
    process.stdin.wait_closed = AsyncMock()
    with (
        patch(
            "anygarden_machine.spawner.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ) as create,
        patch(
            "anygarden_machine.spawner.shutil.which",
            return_value="/bin/anygarden-agent",
        ),
    ):
        try:
            await daemon._handle(frame)
            await asyncio.gather(*list(daemon._spawn_tasks))
        finally:
            pending = list(daemon._spawn_tasks)
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            watchers = [agent.watch_task for agent in list(daemon._spawner._agents.values()) if agent.watch_task]
            await asyncio.gather(*watchers, return_exceptions=True)
        assert not daemon._spawn_tasks
        assert not daemon._spawner._agents
        create.assert_awaited_once()
    args = create.call_args.args
    env = create.call_args.kwargs["env"]
    stdin = process.stdin.write.call_args.args[0].decode()
    assert args[args.index("--provider") + 1] == "local"
    assert "--endpoint-configured" in args
    assert "transport-test-key" not in json.dumps(args)
    assert INPUT_KEY not in env and CONFIG_KEY not in env and CHILD_KEY not in env
    assert json.loads(stdin)[INPUT_KEY] == "transport-test-key"
    persisted = daemon._manifest_store.load(e["agent_id"])
    assert persisted.provider == "local" and persisted.endpoint_configured
    assert persisted.engine_secrets == {}
    # The actual entry point reads private stdin and parses generated arguments.
    monkeypatch.setenv("ANYGARDEN_AGENT_GENERATION", env["ANYGARDEN_AGENT_GENERATION"])
    monkeypatch.setenv("ANYGARDEN_TOKEN", "fixture-agent-token")
    monkeypatch.setenv(INPUT_KEY, "ambient-must-not-win")
    runner = AsyncMock()
    secrets.clear()
    try:
        with patch("anygarden_agent.cli._run_agent", runner):
            result = await asyncio.to_thread(
                CliRunner().invoke, agent_main, list(args[1:]), input=stdin
            )
        assert result.exit_code == 0, result.output
        launch = runner.call_args.kwargs["execution_launch"]
        invocation = Invocation(
            "job",
            SessionScope(
                "n",
                e["agent_id"],
                "n",
                "room-local",
                None,
                "w",
                1,
                1,
                engine="pi-cli",
                engine_version="0.85.1",
            ),
            "hello",
            tmp_path,
            tmp_path,
            provider="stale",
            model="stale",
        )
        bound = launch.bind(invocation)
        assert (bound.provider, bound.model) == ("local", "model-a")
        assert (
            bound.endpoint.credential_ref == ref
            and bound.endpoint.credential_revision == 1
        )
        assert bound.environment[CHILD_KEY] == "transport-test-key"
        assert launch.generation == frame["generation"]
        assert bound.fingerprint != invocation.fingerprint
    finally:
        secrets.clear()
