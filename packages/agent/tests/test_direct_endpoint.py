import json
import os
import tomllib
from dataclasses import asdict, replace

import pytest
from anygarden_agent.runtime.execution.endpoint import (
    CHILD_KEY,
    DirectEndpoint,
    codex_endpoint_arguments,
    endpoint_environment,
    materialize_pi_endpoint,
    parse_endpoint,
)


def endpoint(**overrides):
    return replace(
        DirectEndpoint(
            "my-local", "local-model", "http://127.0.0.1:32100/v1", "responses"
        ),
        **overrides,
    )


def test_endpoint_parser_and_key_are_separate():
    config = endpoint(credential_ref="cred-1", credential_revision=1)
    assert parse_endpoint(json.dumps(asdict(config)), engine="pi-cli") == config
    assert endpoint_environment(config, "secret-fixture") == {
        CHILD_KEY: "secret-fixture"
    }
    assert "secret-fixture" not in repr(config)
    with pytest.raises(ValueError):
        endpoint_environment(config, None)
    with pytest.raises(ValueError):
        endpoint_environment(None, "secret-fixture")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@localhost/v1",
        "http://localhost/v1?api_key=secret",
        "http://localhost/#secret",
        "file:///tmp/server",
        "http://localhost:99999",
        "http://local host",
    ],
)
def test_endpoint_url_rejects_credentials_without_reflecting_input(url):
    with pytest.raises(ValueError) as exc:
        parse_endpoint(json.dumps(asdict(endpoint(base_url=url))), engine="codex-cli")
    assert "secret" not in str(exc.value)
    assert url not in str(exc.value)


def test_protocol_and_closed_config_validation():
    with pytest.raises(ValueError):
        parse_endpoint(
            json.dumps(asdict(endpoint(api_protocol="chat-completions"))),
            engine="codex-cli",
        )
    config = asdict(endpoint()) | {"apiKey": "!execute-command"}
    with pytest.raises(ValueError):
        parse_endpoint(json.dumps(config), engine="pi-cli")


def test_codex_overrides_are_structured_toml_and_keyless_does_not_require_auth():
    cfg = endpoint(credential_ref="cred-1", credential_revision=2)
    args = codex_endpoint_arguments(cfg)
    parsed = tomllib.loads("\n".join(args[1::2]))
    assert parsed["model_provider"] == "ag_direct"
    assert parsed["model_providers"]["ag_direct"]["env_key"] == CHILD_KEY
    assert parsed["model_providers"]["ag_direct"]["wire_api"] == "responses"
    keyless = tomllib.loads("\n".join(codex_endpoint_arguments(endpoint())[1::2]))
    assert "env_key" not in keyless["model_providers"]["ag_direct"]
    assert keyless["model_providers"]["ag_direct"]["requires_openai_auth"] is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX runtime boundary")
def test_pi_config_isolated_rotated_and_removed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    materialize_pi_endpoint(a, endpoint())
    materialize_pi_endpoint(b, endpoint(provider="second-local"))
    target = a / ".pi/agent/models.json"
    assert target.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(target.read_text())["providers"]["my-local"]["apiKey"]
        == f"${CHILD_KEY}"
    )
    materialize_pi_endpoint(a, endpoint(provider="third-local"))
    assert list(json.loads(target.read_text())["providers"]) == ["third-local"]
    assert "second-local" in (b / ".pi/agent/models.json").read_text()
    materialize_pi_endpoint(a, None)
    assert not target.exists()
    assert (b / ".pi/agent/models.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX runtime boundary")
def test_pi_preserves_unmanaged_config_and_refuses_symlink(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".pi").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        materialize_pi_endpoint(home, endpoint())
    assert list(outside.iterdir()) == []
    (home / ".pi").unlink()
    (home / ".pi/agent").mkdir(parents=True)
    target = home / ".pi/agent/models.json"
    target.write_text('{"providers": {}}')
    materialize_pi_endpoint(home, None)
    assert target.exists()


def test_runtime_hooks_reject_missing_key_and_inconsistent_selection(tmp_path):
    from types import SimpleNamespace
    from anygarden_agent.runtime.execution.codex import CodexRuntime
    from anygarden_agent.runtime.execution.pi import PiRuntime
    from anygarden_agent.runtime.execution.endpoint import CHILD_KEY

    endpoint = DirectEndpoint("local", "m", "http://localhost:8000/v1", "responses", "ref", 1)
    invocation = SimpleNamespace(endpoint=endpoint, model="m", provider="local",
        scope=SimpleNamespace(engine="codex-cli"), environment={}, runtime_home=tmp_path)
    with pytest.raises(ValueError, match="credential"):
        CodexRuntime.environment(invocation)
    invocation.environment = {CHILD_KEY: "test-token"}
    invocation.model = "other"
    with pytest.raises(ValueError, match="differs"):
        CodexRuntime.environment(invocation)
    invocation.model = "m"
    invocation.scope.engine = "pi-cli"
    env = PiRuntime.environment(invocation)
    assert env[CHILD_KEY] == "test-token"
    assert "test-token" not in (tmp_path / ".pi/agent/models.json").read_text()
    invocation.endpoint = None
    invocation.environment = {}
    PiRuntime.environment(invocation)
    assert not (tmp_path / ".pi/agent/models.json").exists()


def test_selected_pi_auth_cannot_override_endpoint_and_other_auth_is_preserved(tmp_path):
    directory = tmp_path / ".pi" / "agent"
    directory.mkdir(parents=True)
    auth = directory / "auth.json"
    original = '{"another-provider":{"type":"api_key","key":"other-test-key"}}'
    auth.write_text(original)
    endpoint = DirectEndpoint("local", "m", "http://localhost:8000/v1", "responses")
    materialize_pi_endpoint(tmp_path, endpoint)
    assert auth.read_text() == original
    selected = '{"local":{"type":"api_key","key":"conflicting-test-key"}}'
    auth.write_text(selected)
    with pytest.raises(ValueError, match="conflicts") as error:
        materialize_pi_endpoint(tmp_path, endpoint)
    assert "conflicting-test-key" not in str(error.value)
    assert auth.read_text() == selected


@pytest.mark.skipif(os.name != "posix", reason="POSIX runtime boundary")
def test_pi_existing_user_config_survives_enable_then_disable(tmp_path):
    directory = tmp_path / ".pi/agent"
    directory.mkdir(parents=True)
    target = directory / "models.json"
    original = b'{"providers":{"user-local":{"baseUrl":"http://localhost:9999/v1"}}}\n'
    target.write_bytes(original)
    target.chmod(0o640)
    with pytest.raises(ValueError, match="not managed"):
        materialize_pi_endpoint(tmp_path, endpoint())
    assert target.read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o640
    assert not (directory / ".anygarden-endpoint.sha256").exists()
    materialize_pi_endpoint(tmp_path, None)
    assert target.read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX runtime boundary")
def test_pi_modified_managed_config_survives_change_and_disable(tmp_path):
    materialize_pi_endpoint(tmp_path, endpoint())
    directory = tmp_path / ".pi/agent"
    target = directory / "models.json"
    marker = directory / ".anygarden-endpoint.sha256"
    original_marker = marker.read_bytes()
    modified = target.read_bytes() + b'\n'
    target.write_bytes(modified)
    for next_config in (endpoint(provider="replacement"), endpoint(), None):
        with pytest.raises(ValueError, match="modified"):
            materialize_pi_endpoint(tmp_path, next_config)
        assert target.read_bytes() == modified
        assert marker.read_bytes() == original_marker
