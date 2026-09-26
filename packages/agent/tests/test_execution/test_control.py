"""Durable agent control exercised against actual isolated CLI subprocesses."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace

import pytest
from anygarden_agent.runtime.execution.codex import CodexRuntime
from anygarden_agent.runtime.execution.control import (
    AgentExecutionControl,
    ControlError,
)
from anygarden_agent.runtime.execution.launch import ExecutionLaunch
from anygarden_agent.runtime.execution.pi import PiRuntime

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process ownership")


@pytest.fixture
def local(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    root.mkdir()
    (root / "workspace").mkdir()
    source = tmp_path / "native-auth.json"
    source.write_text('{"tokens":{"access_token":"fixture-only"}}')
    source.chmod(0o600)
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.154.0")
    sys.exit(0)
prompt = sys.stdin.read()
with Path("calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"argv":sys.argv[1:],"env":dict(os.environ),"prompt":prompt}) + "\n")
print(json.dumps({"type":"thread.started","thread_id":"private-native-handle"}), flush=True)
if prompt == "hang":
    time.sleep(60)
print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"answer"}}), flush=True)
print(json.dumps({"type":"turn.completed"}), flush=True)
Path(sys.argv[sys.argv.index("-o")+1]).write_text("answer")
"""
    )
    executable.chmod(0o700)
    monkeypatch.setenv("ANYGARDEN_TOKEN", "SERVER-SECRET")
    monkeypatch.setenv("OPENAI_API_KEY", "AMBIENT-UNSELECTED")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "OTHER-PROVIDER")
    monkeypatch.setenv("PI_SELF_TOOLS_CONFIG", "PRIVATE-SELF-TOOLS")
    launch = [ExecutionLaunch("codex-cli", None, "local-model", 7, None)]

    def create(**kwargs):
        runtime = CodexRuntime(executable)
        runtime.observed_version = "0.154.0"
        return AgentExecutionControl(
            root=root,
            agent_id="agent",
            launch=lambda: launch[0],
            runtime=runtime,
            codex_auth_source=source,
            **kwargs,
        )

    return root, source, launch, create


def request(**kwargs):
    return {
        "execution_id": "execution",
        "execution_node_id": "local-node",
        "authority_node_id": "authority",
        "channel_id": "channel",
        "prompt": "success",
        "policy_epoch": 1,
        "grant_epoch": 2,
        "peer_epoch": 3,
        **kwargs,
    }


def ref(descriptor):
    return {
        "execution_id": descriptor["execution_id"],
        "fingerprint": descriptor["fingerprint"],
    }


async def terminal(control, descriptor):
    async with asyncio.timeout(5):
        while True:
            value = await control.handle("reconcile", ref(descriptor))
            if value["outcome"] is not None:
                return value
            await asyncio.sleep(0.02)


async def test_prepare_restart_start_actual_cli_and_private_environment(local):
    root, source, _launch, create = local
    control = create()
    control.attach("dm")
    descriptor = await control.handle("prepare", request())
    assert set(descriptor) == {"execution_id", "scope", "fingerprint", "generation"}
    assert str(root) not in json.dumps(descriptor)
    assert not (root / "workspace" / "calls.jsonl").exists()
    assert b"fixture-only" not in (control.directory / "prepared.sqlite3").read_bytes()
    assert b"SERVER-SECRET" not in (control.directory / "prepared.sqlite3").read_bytes()
    await control.close()
    control = create()
    control.attach("dm")
    assert await control.handle("prepare", request()) == descriptor
    await control.handle("start", ref(descriptor))
    done = await terminal(control, descriptor)
    assert done["outcome"] == "succeeded" and done["text"] == "answer"
    await control.handle("start", ref(descriptor))
    calls = (root / "workspace" / "calls.jsonl").read_text().splitlines()
    assert len(calls) == 1
    call = json.loads(calls[0])
    assert call["env"]["HOME"] != str(root)
    assert call["env"]["CODEX_HOME"] == call["env"]["HOME"]
    for name in (
        "ANYGARDEN_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "PI_SELF_TOOLS_CONFIG",
    ):
        assert name not in call["env"]
    assert "--ignore-user-config" in call["argv"] and "--ignore-rules" in call["argv"]
    assert "native-handle" not in json.dumps(done)
    assert source.read_text() == '{"tokens":{"access_token":"fixture-only"}}'
    await control.close()


@pytest.mark.parametrize("changed", ["generation", "model", "credential"])
async def test_prepared_config_is_fenced(local, changed):
    _, source, launch, create = local
    control = create()
    control.attach("dm")
    descriptor = await control.handle("prepare", request())
    if changed == "credential":
        source.write_text('{"tokens":{"access_token":"changed"}}')
    else:
        launch[0] = replace(
            launch[0],
            **({"generation": 8} if changed == "generation" else {"model": "other"}),
        )
    with pytest.raises(ControlError, match="GENERATION_CHANGED|CONFIGURATION_CHANGED"):
        await control.handle("start", ref(descriptor))
    await control.close()


async def test_pre_start_cancel_and_crash_gap_never_launch(local):
    root, _, _, create = local
    control = create()
    control.attach("dm")
    first = await control.handle("prepare", request())
    cancelled = await control.handle("cancel", ref(first))
    assert (
        cancelled["outcome"] == "cancelled"
        and cancelled["process_state"] == "not_started"
    )
    with pytest.raises(ControlError, match="POLICY_DENIED"):
        await control.handle("start", ref(first))
    second = await control.handle(
        "prepare", request(execution_id="gap", channel_id="another")
    )
    with control.db:
        control.db.execute("UPDATE prepared SET state='started' WHERE id='gap'")
    await control.close()
    control = create()
    control.attach("dm")
    assert (await control.handle("reconcile", ref(second)))["outcome"] == "unknown"
    with pytest.raises(ControlError, match="EXECUTION_UNKNOWN"):
        await control.handle("start", ref(second))
    assert not (root / "workspace" / "calls.jsonl").exists()
    await control.close()


@pytest.mark.parametrize("stop", ["disconnect", "lease", "revoke"])
async def test_running_process_stops_and_receipt_remains_after_revoke(local, stop):
    root, _, _, create = local
    control = create(lease_seconds=0.2 if stop == "lease" else 20)
    control.attach("dm")
    descriptor = await control.handle("prepare", request(prompt="hang"))
    await control.handle("start", ref(descriptor))
    async with asyncio.timeout(5):
        while not (root / "workspace" / "calls.jsonl").exists():
            await asyncio.sleep(0.01)
    if stop == "disconnect":
        await control.disconnected("dm")
        control.attach("dm")
    elif stop == "lease":
        await asyncio.sleep(0.35)
    else:
        await control.handle("revoke", ref(descriptor))
    done = await terminal(control, descriptor)
    assert done["outcome"] == "cancelled" and done["process_state"] == "stopped"
    with pytest.raises(ControlError, match="POLICY_DENIED"):
        await control.handle("start", ref(descriptor))
    await control.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace", "/tmp"),
        ("model", "remote"),
        ("environment", {"ANYGARDEN_TOKEN": "secret"}),
        ("provider", "remote"),
        ("permission_level", "trusted"),
    ],
)
async def test_remote_configuration_is_not_an_input(local, field, value):
    _, _, _, create = local
    control = create()
    control.attach("dm")
    with pytest.raises(ControlError, match="INVALID_REQUEST"):
        await control.handle("prepare", request(**{field: value}))
    await control.close()


def test_trusted_remote_permission_refused(local):
    _, _, _, create = local
    with pytest.raises(ControlError, match="UNSUPPORTED_PERMISSION"):
        create(permission_level="trusted")


async def test_strict_pi_restricted_tool_list(local):
    _, _, _, create = local
    control = create()
    control.attach("dm")
    invocation = control._invocation(request())
    invocation = replace(
        invocation, scope=replace(invocation.scope, engine="pi-cli"), provider="openai"
    )
    runtime = PiRuntime(control.root / "pi")
    command = runtime.command(invocation, None, control.root / "output")
    assert command[command.index("--tools") + 1] == "read,grep,find,ls"
    assert "--no-extensions" in command and "--no-context-files" in command
    await control.close()


@pytest.mark.parametrize("mode", ["codex-direct", "pi-native", "pi-direct"])
async def test_selected_launch_credentials_in_isolated_strict_runtime(
    local, tmp_path, mode
):
    from anygarden_agent.runtime.execution.endpoint import (
        DirectEndpoint,
        endpoint_environment,
    )
    from anygarden_agent.runtime.execution.pi_auth import INPUT_KEY, NativePiAuth

    root, _, launch, create = local
    engine = "pi-cli" if mode.startswith("pi") else "codex-cli"
    endpoint = (
        None
        if mode == "pi-native"
        else DirectEndpoint(
            "selected",
            "chosen",
            "https://fixture.invalid/v1",
            "responses",
            "credential",
            1,
        )
    )
    private = (
        {INPUT_KEY: "SELECTED-PRIVATE"}
        if mode == "pi-native"
        else endpoint_environment(endpoint, "SELECTED-PRIVATE")
    )
    launch[0] = ExecutionLaunch(
        engine,
        "selected",
        "chosen",
        7,
        endpoint,
        private,
        NativePiAuth("selected", 1) if mode == "pi-native" else None,
    )
    control = create()
    if engine == "pi-cli":
        executable = tmp_path / "fake-pi"
        executable.write_text(
            f"#!{sys.executable}\n"
            + r"""
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print("pi 0.85.1"); sys.exit(0)
if sys.argv[1] == "auth":
    data=json.loads((Path(os.environ["PI_CODING_AGENT_DIR"])/"auth.json").read_text())
    assert data["selected"]["key"] == "SELECTED-PRIVATE"
    print(json.dumps({"provider":"selected","status":"ready"})); sys.exit(0)
assert "--no-extensions" in sys.argv and "--no-context-files" in sys.argv
assert sys.argv[sys.argv.index("--tools")+1] == "read,grep,find,ls"
assert sys.argv[sys.argv.index("--provider")+1] == "selected"
assert sys.argv[sys.argv.index("--model")+1] == "chosen"
assert "ANYGARDEN_TOKEN" not in os.environ and "AG_PI_NATIVE_AUTH_KEY" not in os.environ
assert "ANTHROPIC_API_KEY" not in os.environ and "PI_SELF_TOOLS_CONFIG" not in os.environ
Path("calls.jsonl").write_text(json.dumps({"argv":sys.argv[1:],"env":dict(os.environ)}))
sys.stdin.read()
print(json.dumps({"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"answer"}],"stopReason":"stop"}}))
print(json.dumps({"type":"agent_settled"}))
"""
        )
        executable.chmod(0o700)
        control.runtime = PiRuntime(executable)
        control._manager._runtime = control.runtime
    control.attach("dm")
    descriptor = await control.handle("prepare", request())
    assert not (root / "workspace" / "calls.jsonl").exists()
    assert (
        b"SELECTED-PRIVATE" not in (control.directory / "prepared.sqlite3").read_bytes()
    )
    await control.handle("start", ref(descriptor))
    assert (await terminal(control, descriptor))["outcome"] == "succeeded"
    call = json.loads((root / "workspace" / "calls.jsonl").read_text())
    assert "SELECTED-PRIVATE" not in json.dumps(call["argv"])
    if mode == "pi-native":
        assert "SELECTED-PRIVATE" not in json.dumps(call["env"])
    else:
        assert call["env"]["AG_DIRECT_API_KEY"] == "SELECTED-PRIVATE"
    await control.close()


async def test_cancel_one_execution_preserves_other_work_in_same_scope(local):
    _, _, _, create = local
    control = create()
    control.attach("dm")
    first = await control.handle("prepare", request())
    second = await control.handle("prepare", request(execution_id="second"))
    assert first["scope"] == second["scope"]
    await control.handle("cancel", ref(first))
    await control.handle("start", ref(second))
    assert (await terminal(control, second))["outcome"] == "succeeded"
    await control.close()


def lookup(descriptor):
    return {
        "execution_id": descriptor["execution_id"],
        **{
            key: descriptor["scope"][key]
            for key in (
                "execution_node_id",
                "authority_node_id",
                "channel_id",
            )
        },
    }


async def test_describe_recovers_lost_prepare_ack_after_restart_without_rebinding(
    local,
):
    root, source, launch, create = local
    control = create()
    control.attach("dm")
    lost_ack = await control.handle("prepare", request())
    await control.close()
    # A current agent can recover the old descriptor even if its generation,
    # model or credential changed. No fresh Invocation or credential read is
    # needed to stop old work, and the old fingerprint never authorizes start.
    launch[0] = replace(launch[0], generation=8, model="changed")
    source.unlink()
    control = create()
    control.attach("dm")
    recovered = await control.handle("describe", lookup(lost_ack))
    assert recovered == lost_ack and recovered["generation"] == 7
    assert not control._leases
    assert not (root / "workspace" / "calls.jsonl").exists()
    cancelled = await control.handle("cancel", ref(recovered))
    assert cancelled["outcome"] == "cancelled"
    assert cancelled["process_state"] == "not_started"
    with pytest.raises(ControlError, match="POLICY_DENIED"):
        await control.handle("start", ref(recovered))
    await control.close()


@pytest.mark.parametrize(
    "changed", ["execution_node_id", "authority_node_id", "channel_id"]
)
async def test_describe_requires_exact_owned_scope(local, changed):
    _, _, _, create = local
    control = create()
    control.attach("dm")
    descriptor = await control.handle("prepare", request())
    with pytest.raises(ControlError, match="POLICY_DENIED"):
        await control.handle("describe", {**lookup(descriptor), changed: "other"})
    with pytest.raises(ControlError, match="EXECUTION_UNKNOWN"):
        await control.handle(
            "describe", {**lookup(descriptor), "execution_id": "absent"}
        )
    with pytest.raises(ControlError, match="INVALID_REQUEST"):
        await control.handle("describe", {**lookup(descriptor), "workspace": "/tmp"})
    control.agent_id = "other-agent"
    with pytest.raises(ControlError, match="POLICY_DENIED"):
        await control.handle("describe", lookup(descriptor))
    await control.close()


async def test_describe_cancel_reclaims_orphaned_prepared_capacity(local):
    root, _, _, create = local
    control = create()
    control.attach("dm")
    lost_ack = await control.handle("prepare", request())
    for index in range(31):
        await control.handle("prepare", request(execution_id=f"other-{index}"))
    with pytest.raises(ControlError, match="EXECUTION_QUEUE_FULL"):
        await control.handle("prepare", request(execution_id="next"))
    recovered = await control.handle("describe", lookup(lost_ack))
    await control.handle("cancel", ref(recovered))
    await control.handle("prepare", request(execution_id="next"))
    assert not (root / "workspace" / "calls.jsonl").exists()
    assert not control._leases
    await control.close()


async def test_describe_never_renews_a_running_execution_lease(local):
    _, _, _, create = local
    control = create(lease_seconds=0.2)
    control.attach("dm")
    descriptor = await control.handle("prepare", request(prompt="hang"))
    await control.handle("start", ref(descriptor))
    before = dict(control._leases)
    await asyncio.sleep(0.1)
    assert await control.handle("describe", lookup(descriptor)) == descriptor
    assert control._leases == before
    await asyncio.sleep(0.2)
    assert (await terminal(control, descriptor))["outcome"] == "cancelled"
    await control.close()
