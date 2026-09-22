"""Trusted startup selection and production Invocation binding."""

from dataclasses import replace
import json
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner

from anygarden_agent import secrets
from anygarden_agent.cli import agent_main, _run_agent
from anygarden_agent.runtime.execution.contracts import Invocation, SessionScope
from anygarden_agent.runtime.execution.endpoint import CONFIG_KEY, INPUT_KEY, CHILD_KEY
from anygarden_agent.runtime.execution.launch import load_execution_launch


@pytest.fixture(autouse=True)
def private_secrets():
    secrets.clear()
    yield
    secrets.clear()


def payload(revision=1):
    return {
        CONFIG_KEY: json.dumps(
            dict(
                provider="my-local",
                model="m",
                base_url="http://localhost:8000/v1",
                api_protocol="responses",
                credential_ref="ref",
                credential_revision=revision,
            )
        ),
        INPUT_KEY: "private-test-key",
    }


def invocation(tmp_path):
    return Invocation(
        "job",
        SessionScope(
            "n",
            "a",
            "n",
            "r",
            None,
            "w",
            1,
            7,
            engine="pi-cli",
            engine_version="0.85.1",
        ),
        "hello",
        tmp_path,
        tmp_path,
        provider="old",
        model="old",
    )


def test_private_launch_binding_fingerprint_and_session_epoch(tmp_path, monkeypatch):
    monkeypatch.setenv(INPUT_KEY, "ambient-key")
    secrets.set_secrets(payload())
    config = load_execution_launch(
        engine="pi-cli",
        provider="my-local",
        model="m",
        generation=4,
        endpoint_configured=True,
    )
    base = invocation(tmp_path)
    bound = config.bind(base)
    assert bound.provider == "my-local" and bound.model == "m"
    assert bound.environment[CHILD_KEY] == "private-test-key"
    assert "private-test-key" not in repr(config)
    assert bound.endpoint.credential_revision == 1
    assert bound.scope.key != base.scope.key
    assert config.bind(base).scope.key == bound.scope.key
    assert (
        config.bind(replace(base, scope=replace(base.scope, policy_epoch=8))).scope.key
        != bound.scope.key
    )
    secrets.set_secrets(payload(2))
    rotated = load_execution_launch(
        engine="pi-cli",
        provider="my-local",
        model="m",
        generation=5,
        endpoint_configured=True,
    ).bind(base)
    assert (
        rotated.fingerprint != bound.fingerprint
        and rotated.scope.key != bound.scope.key
    )
    assert (
        replace(
            bound, endpoint=replace(bound.endpoint, credential_revision=2)
        ).fingerprint
        != bound.fingerprint
    )


def test_ambient_cannot_restore_missing_private_config(monkeypatch):
    for key, value in payload().items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="private stdin"):
        load_execution_launch(
            engine="pi-cli",
            provider="my-local",
            model="m",
            generation=1,
            endpoint_configured=True,
        )


@pytest.mark.parametrize("provider,model", [("other", "m"), ("my-local", "wrong")])
def test_launch_selection_must_match_private_descriptor(provider, model):
    secrets.set_secrets(payload())
    with pytest.raises(ValueError, match="differs"):
        load_execution_launch(
            engine="pi-cli", provider=provider, model=model, generation=1
        )


def test_cli_consumes_provider_and_private_payload(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_GENERATION", "11")
    run = AsyncMock()
    with patch("anygarden_agent.cli._run_agent", run):
        result = CliRunner().invoke(
            agent_main,
            [
                "--engine",
                "pi-cli",
                "--provider",
                "my-local",
                "--model",
                "m",
                "--name",
                "a",
                "--server",
                "ws://localhost",
                "--room",
                "r",
                "--token",
                "test-agent-token",
                "--endpoint-configured",
            ],
            input=json.dumps(payload()),
        )
    assert result.exit_code == 0, result.output
    launch = run.call_args.kwargs["execution_launch"]
    assert launch.provider == "my-local" and launch.generation == 11
    assert launch.endpoint.credential_revision == 1


async def test_legacy_engine_cannot_ignore_direct_endpoint():
    secrets.set_secrets(payload())
    launch = load_execution_launch(
        engine="codex-cli", provider="my-local", model="m", generation=1
    )
    with (
        patch("anygarden_agent.cli._setup_engine", new_callable=AsyncMock),
        patch(
            "anygarden_agent.client.ChatClient.join_room", new_callable=AsyncMock
        ) as join,
    ):
        with pytest.raises(Exception, match="legacy engine fallback refused"):
            await _run_agent(
                "codex-cli",
                "a",
                "ws://localhost",
                "t",
                ["r"],
                "m",
                None,
                execution_launch=launch,
            )
    join.assert_not_awaited()
