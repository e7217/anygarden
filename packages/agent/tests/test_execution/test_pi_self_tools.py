"""Pi self tools: no provider requests, only fixture HTTP and extension calls."""

import asyncio
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from anygarden_agent.integrations.room_execution import RoomExecutionAdapter
from anygarden_agent.runtime.execution.contracts import Invocation, SessionScope
from anygarden_agent.runtime.execution.launch import ExecutionLaunch
from anygarden_agent.runtime.execution.pi import PiRuntime
from anygarden_agent.runtime.execution.pi_self_tools import (
    CONFIG_ENV,
    EXTENSION_PATH,
    TOKEN_ENV,
    TOOL_NAMES,
    prepare_pi_self_tools,
    self_mcp_url,
)
from anygarden_agent.runtime.execution.room import RoomInvocation, RoomPiRuntime

TOKEN = "agt_self-tools-fixture"
HARNESS = Path(__file__).parents[1] / "fixtures" / "pi_self_tools_harness.mjs"


def tool_list():
    return {
        "jsonrpc": "2.0",
        "id": "pi-tools",
        "result": {
            "tools": [
                {
                    "name": name,
                    "description": name,
                    "inputSchema": {"type": "object", "properties": {}},
                }
                for name in TOOL_NAMES
            ]
        },
    }


async def prepared(tmp_path, payload=None):
    return await prepare_pi_self_tools(
        tmp_path,
        server_url="ws://fixture",
        token=TOKEN,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload or tool_list())
        ),
    )


@pytest.mark.asyncio
async def test_prepare_uses_only_self_token_and_writes_no_credentials(tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=tool_list())

    path = await prepare_pi_self_tools(
        tmp_path,
        server_url="wss://cluster.example/prefix/",
        token=TOKEN,
        transport=httpx.MockTransport(handle),
    )
    assert str(requests[0].url) == "https://cluster.example/prefix/mcp/rpc"
    assert requests[0].headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(requests[0].content)["method"] == "tools/list"
    config = json.loads(path.read_text())
    assert {tool["name"] for tool in config["tools"]} == TOOL_NAMES
    assert TOKEN not in path.read_text()
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/server",
        "https://token@example.com",
        "http://server?token=secret",
        "http://server/#x",
        "http://server/\n",
    ],
)
def test_rejects_implicit_or_credentialed_server_urls(url):
    with pytest.raises(ValueError, match="explicit"):
        self_mcp_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "transport-jwt", "agt_bad\nheader"])
async def test_missing_self_token_never_falls_back_to_transport(
    tmp_path, token, monkeypatch
):
    monkeypatch.setenv("ANYGARDEN_TOKEN", "transport-jwt")
    with pytest.raises(ValueError, match="credential is missing"):
        await prepare_pi_self_tools(tmp_path, server_url="http://fixture", token=token)
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 302, 503])
async def test_read_failure_is_explicit_and_redirects_are_not_followed(
    tmp_path, status
):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"location": "http://other-server"}, text=TOKEN
        )

    with pytest.raises(ValueError) as error:
        await prepare_pi_self_tools(
            tmp_path,
            server_url="http://fixture",
            token=TOKEN,
            transport=httpx.MockTransport(handle),
        )
    assert TOKEN not in str(error.value)
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_incomplete_schemas_and_symlinked_config_directory_are_rejected(tmp_path):
    payload = tool_list()
    payload["result"]["tools"].pop()
    with pytest.raises(ValueError, match="required"):
        await prepared(tmp_path, payload)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".anygarden-execution").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        await prepared(tmp_path)
    assert not list(outside.iterdir())


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required by Pi")
async def test_extension_registers_all_tools_and_maps_errors_and_cancellation(tmp_path):
    config = await prepared(tmp_path)
    calls = [
        {"name": "create_task", "mock": mode}
        for mode in [
            "http",
            "rpc",
            "tool",
            "invalid",
            "oversized",
            "network",
            "cancel",
            "timeout",
        ]
    ]
    child = await asyncio.create_subprocess_exec(
        "node",
        str(HARNESS),
        str(EXTENSION_PATH),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, TOKEN_ENV: TOKEN, CONFIG_ENV: str(config)},
    )
    stdout, stderr = await asyncio.wait_for(
        child.communicate(json.dumps({"calls": calls}).encode()), 10
    )
    assert child.returncode == 0, stderr.decode()
    output = json.loads(stdout)
    assert set(output["names"]) == TOOL_NAMES
    assert all(not result["ok"] for result in output["results"])
    assert "HTTP 403" in output["results"][0]["error"]
    assert "[redacted]" in output["results"][1]["error"]
    assert "[redacted]" in output["results"][2]["error"]
    assert "cancelled" in output["results"][-2]["error"]
    assert "timed out" in output["results"][-1]["error"]
    assert TOKEN.encode() not in stdout + stderr


@pytest.mark.parametrize("tier", ["standard", "trusted", "restricted"])
def test_local_tool_loading_and_restricted_remote_boundaries(tmp_path, tier):
    scope = SessionScope(
        "n", "a", "n", "r", None, "w", 0, 0, engine="pi-cli", engine_version="0.85.1"
    )
    invocation = RoomInvocation(
        "one",
        scope,
        "test",
        tmp_path,
        tmp_path,
        provider="zai",
        permission_level=tier,
        environment={TOKEN_ENV: TOKEN, "ANYGARDEN_TOKEN": "transport"},
    )
    runtime = RoomPiRuntime(Path("/fake/pi"))
    runtime.self_tools_config = tmp_path / "config.json"
    command = runtime.command(invocation, None, tmp_path / "out")
    environment = runtime.environment(invocation)
    assert "ANYGARDEN_TOKEN" not in environment
    if tier == "restricted":
        assert command[-2:] == ["--tools", "read,grep,find,ls"]
        assert "--extension" not in command
        assert TOKEN_ENV not in environment and CONFIG_ENV not in environment
    else:
        assert command[-2:] == ["--extension", str(EXTENSION_PATH)]
        assert environment[TOKEN_ENV] == TOKEN
        assert environment[CONFIG_ENV] == str(runtime.self_tools_config)
    remote = Invocation("two", scope, "test", tmp_path, tmp_path, provider="zai")
    isolated = PiRuntime(Path("/fake/pi"))
    assert "--no-extensions" in isolated.command(remote, None, tmp_path / "out")
    assert "--extension" not in isolated.command(remote, None, tmp_path / "out")
    with pytest.raises(ValueError, match="credentials"):
        isolated.environment(replace(remote, environment={TOKEN_ENV: TOKEN}))


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["standard", "trusted", "restricted"])
async def test_room_start_prepares_tools_with_explicit_server_and_self_token(
    tmp_path, monkeypatch, tier
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "anygarden_agent.integrations.room_execution.resolve_engine_executable",
        lambda _: "/fake/pi",
    )
    monkeypatch.setattr(
        "anygarden_agent.integrations.room_execution.staged_environment",
        lambda: {TOKEN_ENV: TOKEN},
    )
    prepare = AsyncMock(return_value=tmp_path / "config.json")
    monkeypatch.setattr(
        "anygarden_agent.runtime.execution.pi_self_tools.prepare_pi_self_tools", prepare
    )
    adapter = RoomExecutionAdapter(engine="pi-cli", permission_level=tier)
    adapter._client = SimpleNamespace(
        execution_launch=ExecutionLaunch("pi-cli", "zai", "model", 1, None),
        _server_url="wss://cluster.example",
        _agent_id="agent",
        _generation=1,
        _token="transport-secret",
    )
    await adapter.start()
    if tier == "restricted":
        prepare.assert_not_called()
    else:
        prepare.assert_awaited_once_with(
            tmp_path, server_url="wss://cluster.example", token=TOKEN
        )
