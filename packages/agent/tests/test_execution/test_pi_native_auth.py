"""Native Pi credentials stay inside one agent's runtime home."""

import asyncio
import json
import sys
from dataclasses import replace

import pytest
from anygarden_agent.runtime.execution.contracts import Invocation, SessionScope
from anygarden_agent.runtime.execution.endpoint import (
    CHILD_KEY,
    KEYLESS_PLACEHOLDER,
    DirectEndpoint,
)
from anygarden_agent.runtime.execution.pi import PiRuntime
from anygarden_agent.runtime.execution.pi_auth import (
    INPUT_KEY,
    NativePiAuth,
    materialize_native_auth,
)


def test_native_auth_materializes_and_rotates_only_selected_provider(tmp_path):
    home = tmp_path / "agent-a"
    home.mkdir()
    selected = NativePiAuth("zai", 1)
    materialize_native_auth(home, selected, "secret-one")
    path = home / ".pi" / "agent" / "auth.json"
    assert json.loads(path.read_text()) == {
        "zai": {"type": "api_key", "key": "secret-one"}
    }
    assert path.stat().st_mode & 0o777 == 0o600
    path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions are unsafe"):
        materialize_native_auth(home, selected, "secret-one")
    path.chmod(0o600)
    materialize_native_auth(home, NativePiAuth("zai", 2), "secret-two")
    assert json.loads(path.read_text())["zai"]["key"] == "secret-two"
    materialize_native_auth(home, None, None)
    assert "zai" not in json.loads(path.read_text())


def test_native_auth_rejects_conflicting_or_symlinked_file(tmp_path):
    home = tmp_path / "agent-a"
    directory = home / ".pi" / "agent"
    directory.mkdir(parents=True)
    path = directory / "auth.json"
    path.write_text(json.dumps({"zai": {"type": "api_key", "key": "manual"}}))
    with pytest.raises(ValueError, match="existing"):
        materialize_native_auth(home, NativePiAuth("zai", 1), "secret")
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_text("{}")
    path.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        materialize_native_auth(home, NativePiAuth("zai", 1), "secret")
    assert outside.read_text() == "{}"


def test_native_auth_does_not_touch_other_agent(tmp_path):
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir()
    home_b.mkdir()
    materialize_native_auth(home_a, NativePiAuth("zai", 1), "secret-a")
    materialize_native_auth(home_b, NativePiAuth("zai", 1), "secret-b")
    assert "secret-b" not in (home_a / ".pi" / "agent" / "auth.json").read_text()
    assert "secret-a" not in (home_b / ".pi" / "agent" / "auth.json").read_text()


@pytest.mark.parametrize(
    "key,stored", [("$literal", "$$literal"), ("!literal", "$!literal")]
)
def test_native_auth_escapes_pi_config_syntax(tmp_path, key, stored):
    home = tmp_path / "agent"
    home.mkdir()
    materialize_native_auth(home, NativePiAuth("zai", 1), key)
    path = home / ".pi" / "agent" / "auth.json"
    assert json.loads(path.read_text())["zai"]["key"] == stored
    materialize_native_auth(home, NativePiAuth("zai", 1), key)
    assert json.loads(path.read_text())["zai"]["key"] == stored


@pytest.mark.asyncio
async def test_preflight_uses_agent_auth_and_maps_pi_reasons(tmp_path):
    home, workspace = tmp_path / "agent", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    executable = tmp_path / "fake-pi"
    executable.write_text(
        f"#!{sys.executable}\n" + "import json, os, sys\n"
        "from pathlib import Path\n"
        "if sys.argv[1:] == ['--version']:\n"
        " print('pi 0.85.1'); sys.exit(0)\n"
        "if sys.argv[1] == '--print':\n"
        " print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'answer'}], 'stopReason': 'stop'}}))\n"
        " print(json.dumps({'type': 'agent_settled'})); sys.exit(0)\n"
        "assert sys.argv[1:4] == ['auth', 'check', '--provider']\n"
        "assert 'AG_PI_NATIVE_AUTH_KEY' not in os.environ\n"
        "path = Path(os.environ['PI_CODING_AGENT_DIR']) / 'auth.json'\n"
        "credential = json.loads(path.read_text()).get('zai') if path.exists() else None\n"
        "status = os.environ.get('FAKE_AUTH_STATUS', 'ready')\n"
        "if not credential: status = 'missing'\n"
        "result = {'status': 'ready', 'provider': 'zai', 'authType': 'api_key'} if status == 'ready' else "
        "{'status': 'not_ready', 'provider': 'zai', 'reason': 'provider_not_found' if status == 'unknown' else 'credentials_not_configured'}\n"
        "print(json.dumps(result))\n"
        "sys.exit(0 if result['status'] == 'ready' else 1)\n"
    )
    executable.chmod(0o700)
    host_dir = tmp_path / "service-user" / ".pi" / "agent"
    host_dir.mkdir(parents=True)
    (host_dir / "auth.json").write_text(
        json.dumps({"zai": {"type": "api_key", "key": "host-key"}})
    )
    command = [
        str(executable),
        "auth",
        "check",
        "--provider",
        "zai",
        "--model",
        "glm-5.3-flash",
        "--json",
        "--no-refresh",
    ]
    host_check = await asyncio.create_subprocess_exec(
        *command,
        env={"PI_CODING_AGENT_DIR": str(host_dir)},
        stdout=asyncio.subprocess.PIPE,
    )
    host_output, _ = await host_check.communicate()
    assert json.loads(host_output)["status"] == "ready"
    isolated_check = await asyncio.create_subprocess_exec(
        *command,
        env={"PI_CODING_AGENT_DIR": str(home / ".pi" / "agent")},
        stdout=asyncio.subprocess.PIPE,
    )
    isolated_output, _ = await isolated_check.communicate()
    assert json.loads(isolated_output)["reason"] == "credentials_not_configured"
    scope = SessionScope(
        "node",
        "agent",
        "node",
        "room",
        None,
        "workspace",
        0,
        0,
        engine="pi-cli",
        engine_version="0.85.1",
    )
    invocation = Invocation(
        "turn",
        scope,
        "hello",
        workspace,
        home,
        provider="zai",
        model="glm-5.3-flash",
        native_auth=NativePiAuth("zai", 1),
        environment={INPUT_KEY: "secret"},
    )
    runtime = PiRuntime(executable)
    assert await runtime.preflight(invocation) is None
    assert (
        await runtime.preflight(
            replace(
                invocation,
                environment={**invocation.environment, "FAKE_AUTH_STATUS": "unknown"},
            )
        )
        == "UNKNOWN_PROVIDER"
    )
    assert (
        await runtime.preflight(
            replace(
                invocation,
                environment={**invocation.environment, "FAKE_AUTH_STATUS": "missing"},
            )
        )
        == "AUTH_MISSING"
    )
    assert "secret" not in runtime.environment(invocation).values()
    result = await runtime.run(
        invocation, None, lambda *_: None, lambda *_: None, lambda: True
    )
    assert result.outcome == "succeeded" and result.text == "answer"
    rotated = replace(
        invocation,
        native_auth=NativePiAuth("zai", 2),
        environment={INPUT_KEY: "rotated"},
    )
    result = await runtime.run(
        rotated, None, lambda *_: None, lambda *_: None, lambda: True
    )
    assert result.outcome == "succeeded"
    assert (
        json.loads((home / ".pi" / "agent" / "auth.json").read_text())["zai"]["key"]
        == "rotated"
    )
    direct = DirectEndpoint(
        "zai", "glm-5.3-flash", "http://localhost:8000/v1", "chat-completions"
    )
    direct_invocation = replace(
        rotated,
        native_auth=None,
        endpoint=direct,
        environment={CHILD_KEY: KEYLESS_PLACEHOLDER},
    )
    runtime.environment(direct_invocation)
    assert "zai" not in json.loads((home / ".pi" / "agent" / "auth.json").read_text())
    assert (home / ".pi" / "agent" / "models.json").exists()
    runtime.environment(rotated)
    assert (
        json.loads((home / ".pi" / "agent" / "auth.json").read_text())["zai"]["key"]
        == "rotated"
    )
    assert not (home / ".pi" / "agent" / "models.json").exists()
