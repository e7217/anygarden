from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "engine_smoke_gate.py"
SPEC = importlib.util.spec_from_file_location("engine_smoke_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def configured_env() -> dict[str, str]:
    sha = "a" * 40
    return {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": sha,
        "GITHUB_RUN_ID": "1234",
        "ANYGARDEN_DEFAULT_BRANCH": "main",
        "ANYGARDEN_CHECKOUT_SHA": sha,
        "ANYGARDEN_SMOKE_ENVIRONMENT_PROTECTED": "true",
        "ANYGARDEN_SMOKE_APPROVED": "true",
        "ANYGARDEN_SMOKE_BUDGET_POLICY": "vendor-daily-cap-1",
        "ANYGARDEN_SMOKE_EGRESS_POLICY": "vendor-only",
        "ANYGARDEN_SMOKE_PROXY_URL": "http://192.168.100.97:3129",
        "HTTP_PROXY": "http://192.168.100.97:3129",
        "HTTPS_PROXY": "http://192.168.100.97:3129",
        "ANYGARDEN_SMOKE_CREDENTIAL_SCOPE": "low-privilege-test-only",
        "ANYGARDEN_SMOKE_CONTAINER_IMAGE": (
            "ghcr.io/e7217/anygarden-smoke@sha256:" + "b" * 64
        ),
        "ANYGARDEN_SMOKE_MODEL": "gpt-5.4-mini",
        "OPENAI_API_KEY": "must-never-appear",
    }


def test_missing_configuration_is_blocked_and_redacted(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"

    assert smoke.run("preflight", evidence, {}) == 2

    payload = evidence.read_text()
    assert json.loads(payload)["result_code"] == "BLOCKED_CONFIGURATION"
    assert "prompt" not in payload
    assert "response" not in payload
    assert "credential" not in payload


def test_blocked_configuration_never_spawns_engine(tmp_path: Path, monkeypatch) -> None:
    evidence = tmp_path / "evidence.json"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("engine must not start")

    monkeypatch.setattr(smoke, "execute", forbidden)
    assert smoke.run("run", evidence, {}) == 2
    assert json.loads(evidence.read_text())["result_code"] == "BLOCKED_CONFIGURATION"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_EVENT_NAME", "pull_request_target"),
        ("GITHUB_REF", "refs/pull/1/merge"),
        ("ANYGARDEN_CHECKOUT_SHA", "b" * 40),
        ("ANYGARDEN_SMOKE_ENVIRONMENT_PROTECTED", "false"),
        ("ANYGARDEN_SMOKE_BUDGET_POLICY", "none"),
        ("ANYGARDEN_SMOKE_EGRESS_POLICY", "internet"),
        ("ANYGARDEN_SMOKE_PROXY_URL", ""),
        ("ANYGARDEN_SMOKE_PROXY_URL", "https://192.168.100.97:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://user@192.168.100.97:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://8.8.8.8:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://172.15.255.255:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://172.32.0.0:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://192.0.2.1:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://198.51.100.1:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://203.0.113.1:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://240.0.0.1:3129"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://192.168.100.97"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://192.168.100.97:0"),
        ("ANYGARDEN_SMOKE_PROXY_URL", "http://192.168.100.97:3129/path"),
        ("ANYGARDEN_SMOKE_CONTAINER_IMAGE", "ghcr.io/e7217/smoke:latest"),
    ],
)
def test_preflight_fails_closed_for_unsafe_context(
    tmp_path: Path, key: str, value: str
) -> None:
    env = configured_env()
    env[key] = value
    evidence = tmp_path / "evidence.json"

    assert smoke.run("preflight", evidence, env) == 2
    assert json.loads(evidence.read_text())["result_code"] == "BLOCKED_CONFIGURATION"


def test_preflight_evidence_has_only_redacted_contract_fields(tmp_path: Path) -> None:
    env = configured_env()
    credential = env.pop("OPENAI_API_KEY")
    evidence = tmp_path / "evidence.json"

    assert smoke.run("preflight", evidence, env) == 0

    payload = json.loads(evidence.read_text())
    assert set(payload) == {
        "duration_ms",
        "engine",
        "engine_version",
        "exact_sha",
        "failure_category",
        "input_sha256",
        "model_version",
        "output_length",
        "output_sha256",
        "result_code",
        "workflow_run",
    }
    assert payload["result_code"] == "PREFLIGHT_PASS"
    assert payload["failure_category"] == "NOT_APPLICABLE"
    assert payload["exact_sha"] == env["GITHUB_SHA"]
    assert payload["input_sha256"] == smoke._sha256(smoke.CANARY_PROMPT.encode())
    assert credential not in evidence.read_text()
    assert smoke.CANARY_PROMPT not in evidence.read_text()


def test_live_runner_requires_credential_only_after_isolation(
    tmp_path: Path, monkeypatch
) -> None:
    env = configured_env()
    env.pop("OPENAI_API_KEY")
    env["ANYGARDEN_SMOKE_RUNTIME_ISOLATED"] = "container-readonly-empty-workspace"
    monkeypatch.chdir(tmp_path)

    with pytest.raises(smoke.BlockedConfiguration):
        smoke.execute(env["ANYGARDEN_SMOKE_MODEL"], env)


def test_command_is_fixed_read_only_ephemeral_single_turn() -> None:
    command = smoke.build_command("gpt-5.4-mini")

    assert command.count("exec") == 1
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "read-only" in command
    assert "approval_policy=untrusted" in command
    assert "model_reasoning_effort=minimal" in command
    assert command[-1] == "-"
    assert "resume" not in command
    assert smoke.HARD_TIMEOUT_SECONDS == 60
    assert smoke.MAX_RESPONSE_BYTES == 256


def test_child_env_passes_only_validated_proxy_endpoint() -> None:
    env = configured_env()
    runtime_state = {"HOME": "/tmp/home", "CODEX_HOME": "/tmp/codex"}

    child_env = smoke.build_child_env(
        env,
        env["OPENAI_API_KEY"],
        runtime_state,
        smoke.validate_proxy_url(env),
    )

    assert child_env["HTTP_PROXY"] == env["ANYGARDEN_SMOKE_PROXY_URL"]
    assert child_env["HTTPS_PROXY"] == env["ANYGARDEN_SMOKE_PROXY_URL"]
    assert child_env["http_proxy"] == env["ANYGARDEN_SMOKE_PROXY_URL"]
    assert child_env["https_proxy"] == env["ANYGARDEN_SMOKE_PROXY_URL"]
    assert "ALL_PROXY" not in child_env
    assert "NO_PROXY" not in child_env


@pytest.mark.parametrize(
    "host",
    [
        "10.0.0.1",
        "10.255.255.254",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "192.168.255.254",
    ],
)
def test_proxy_url_accepts_only_rfc1918_networks(host: str) -> None:
    env = configured_env()
    env["ANYGARDEN_SMOKE_PROXY_URL"] = f"http://{host}:3129"

    assert smoke.validate_proxy_url(env) == env["ANYGARDEN_SMOKE_PROXY_URL"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("HTTP_PROXY", ""),
        ("HTTPS_PROXY", ""),
        ("HTTP_PROXY", "http://192.168.100.98:3129"),
        ("HTTPS_PROXY", "http://192.168.100.98:3129"),
    ],
)
def test_proxy_transport_must_match_validated_endpoint(key: str, value: str) -> None:
    env = configured_env()
    env[key] = value

    with pytest.raises(smoke.BlockedConfiguration):
        smoke.validate_proxy_transport(env, smoke.validate_proxy_url(env))


def test_runtime_state_dirs_are_created_privately_on_fixed_tmpfs(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_root = tmp_path / "tmpfs"
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    env = {
        "HOME": str(runtime_root / "home"),
        "CODEX_HOME": str(runtime_root / "codex"),
    }

    runtime_state = smoke.prepare_runtime_state(env)

    assert runtime_state == env
    for path in (runtime_root / "home", runtime_root / "codex"):
        assert path.is_dir()
        assert path.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "env",
    [
        {"HOME": "/work/home", "CODEX_HOME": "/tmp/codex"},
        {"HOME": "/tmp/home", "CODEX_HOME": "/work/codex"},
        {"HOME": "/tmp/home"},
    ],
)
def test_runtime_state_rejects_missing_or_non_tmpfs_paths(
    tmp_path: Path, monkeypatch, env: dict[str, str]
) -> None:
    runtime_root = tmp_path / "tmpfs"
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    mapped_env = {
        key: value.replace("/tmp", str(runtime_root)) for key, value in env.items()
    }

    with pytest.raises(smoke.BlockedConfiguration):
        smoke.prepare_runtime_state(mapped_env)


def test_response_parser_accepts_only_exact_canary() -> None:
    raw = b"\n".join(
        [
            b'{"type":"thread.started","thread_id":"secret-session"}',
            b'{"type":"item.completed","item":{"type":"reasoning","text":"hidden"}}',
            b'{"type":"item.completed","item":{"type":"agent_message","text":"ANYGARDEN_SMOKE_OK"}}',
            b'{"type":"turn.completed","usage":{"output_tokens":4}}',
        ]
    )

    assert smoke.parse_response(raw) == b"ANYGARDEN_SMOKE_OK"


@pytest.mark.parametrize(
    "raw",
    [
        b'{"type":"item.completed","item":{"type":"command_execution"}}',
        b'{"type":"approval.requested"}',
        b'{"type":"item.completed","item":{"type":"agent_message","text":"almost"}}',
        (
            b'{"type":"item.completed","item":{"type":"agent_message","text":"'
            + b"x" * 257
            + b'"}}'
        ),
    ],
)
def test_response_parser_rejects_tools_approval_mismatch_and_oversize(
    raw: bytes,
) -> None:
    with pytest.raises(smoke.SmokeFailure):
        smoke.parse_response(raw)


@pytest.mark.parametrize(
    ("raw", "category"),
    [
        (
            (
                b'{"type":"error","message":"unexpected status 401 '
                b'Unauthorized: provider-secret, url: https://example.invalid"}'
            ),
            "AUTHENTICATION",
        ),
        (
            (
                b'{"type":"turn.failed","error":{"message":"unexpected '
                b'status 404 Not Found: Model not found provider-secret"}}'
            ),
            "MODEL",
        ),
        (
            (
                b'{"type":"error","message":"exceeded retry limit, last '
                b'status: 429 Too Many Requests, request id: provider-secret"}'
            ),
            "RATE_LIMIT",
        ),
        (
            (
                b'{"type":"turn.failed","error":{"message":"unexpected '
                b'status 503 Service Unavailable: provider-secret"}}'
            ),
            "UPSTREAM",
        ),
        (
            (
                b'{"type":"error","message":"You\'ve hit your usage '
                b'limit. provider-secret"}'
            ),
            "RATE_LIMIT",
        ),
        (
            b'{"type":"error","message":"Connection failed: provider-secret"}',
            "UPSTREAM",
        ),
        (
            (
                b'{"type":"error","message":"unexpected status 503 '
                b'Service Unavailable: Model not found provider-secret"}'
            ),
            "UPSTREAM",
        ),
        (
            (
                b'{"type":"error","message":"unexpected status 401 '
                b'Unauthorized: Model not found provider-secret"}'
            ),
            "AUTHENTICATION",
        ),
        (
            (
                b'{"type":"item.completed","item":{"type":"agent_message",'
                b'"text":"unexpected status 401 Unauthorized"}}'
            ),
            "UNKNOWN",
        ),
    ],
)
def test_failure_classifier_projects_only_closed_categories(
    raw: bytes, category: str
) -> None:
    assert smoke.classify_failure(raw) == category
    assert category in smoke.FAILURE_CATEGORIES


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b'{"type":"error","message":42}',
        b'{"type":"turn.failed","error":"not-an-object"}',
        b'{"type":"error","message":"provider-secret"}',
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized: '
            + b"x" * smoke.MAX_FAILURE_MESSAGE_BYTES
            + b'"}'
        ),
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized"}\n'
            b'{"type":"turn.failed","error":{"message":"unexpected status '
            b'503 Service Unavailable"}}'
        ),
        (
            b'{"type":"error","message":"unrecognized provider-secret"}\n'
            b'{"type":"turn.failed","error":{"message":"unexpected status '
            b'401 Unauthorized"}}'
        ),
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized"}'
            + b" " * (smoke.MAX_FAILURE_EVENT_BYTES + 1)
        ),
    ],
)
def test_failure_classifier_collapses_unsafe_input_to_unknown(raw: bytes) -> None:
    assert smoke.classify_failure(raw) == "UNKNOWN"


def test_evidence_boundary_collapses_unlisted_category_without_leaking() -> None:
    sensitive_category = "AUTHENTICATION:raw-provider-secret"

    evidence = smoke.make_evidence(
        configured_env(),
        "FAIL_ENGINE_NONZERO",
        0,
        failure_category=sensitive_category,
    )

    assert evidence.failure_category == "UNKNOWN"
    assert sensitive_category not in json.dumps(evidence.__dict__)


@pytest.mark.parametrize(
    ("stdout", "stderr", "category"),
    [
        (
            (
                b'{"type":"turn.failed","error":{"message":"unexpected '
                b'status 401 Unauthorized: raw-provider-secret"}}'
            ),
            b"raw-stderr-secret",
            "AUTHENTICATION",
        ),
        (
            b'{"type":"turn.failed","error":{"message":"unknown raw failure"}}',
            b"unexpected status 401 Unauthorized: stderr-only-secret",
            "UNKNOWN",
        ),
    ],
)
def test_nonzero_engine_evidence_keeps_only_projected_category(
    tmp_path: Path,
    monkeypatch,
    stdout: bytes,
    stderr: bytes,
    category: str,
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/usr/bin/codex")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"codex-cli 0.146.0"
        ),
    )

    class FakeProcess:
        returncode = 1

        def communicate(self, **_kwargs):
            return stdout, stderr

    monkeypatch.setattr(
        smoke.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess()
    )
    env = configured_env()
    env.update(
        {
            "ANYGARDEN_SMOKE_RUNTIME_ISOLATED": ("container-readonly-empty-workspace"),
            "HOME": str(runtime_root / "home"),
            "CODEX_HOME": str(runtime_root / "codex"),
        }
    )
    evidence = tmp_path / "evidence.json"

    assert smoke.run("run", evidence, env) == 1

    payload = evidence.read_text()
    decoded = json.loads(payload)
    assert decoded["result_code"] == "FAIL_ENGINE_NONZERO"
    assert decoded["failure_category"] == category
    assert stdout.decode() not in payload
    assert stderr.decode() not in payload
    assert "raw-provider-secret" not in payload
    assert "raw-stderr-secret" not in payload
    assert "stderr-only-secret" not in payload


def test_runtime_requires_isolated_empty_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(smoke.BlockedConfiguration):
        smoke.execute("gpt-5.4-mini", {"OPENAI_API_KEY": "x"})


@pytest.mark.parametrize(
    ("error", "exit_code", "result_code", "failure_category"),
    [
        (TimeoutError("raw response"), 124, "TIMEOUT", "NOT_APPLICABLE"),
        (
            smoke.SmokeFailure("engine_nonzero"),
            1,
            "FAIL_ENGINE_NONZERO",
            "UNKNOWN",
        ),
        (
            smoke.SmokeFailure("embedded-sensitive-value"),
            1,
            "FAIL",
            "NOT_APPLICABLE",
        ),
        (
            RuntimeError("/home/runner embedded-sensitive-value"),
            1,
            "FAIL",
            "NOT_APPLICABLE",
        ),
    ],
)
def test_failure_evidence_never_contains_exception_text(
    tmp_path: Path,
    monkeypatch,
    error: Exception,
    exit_code: int,
    result_code: str,
    failure_category: str,
) -> None:
    env = configured_env()
    evidence = tmp_path / "evidence.json"

    def fail(_model: str, _env: dict[str, str]) -> tuple[bytes, str]:
        raise error

    monkeypatch.setattr(smoke, "execute", fail)
    assert smoke.run("run", evidence, env) == exit_code
    payload = evidence.read_text()
    decoded = json.loads(payload)
    assert decoded["result_code"] == result_code
    assert decoded["failure_category"] == failure_category
    assert str(error) not in payload
    assert env["OPENAI_API_KEY"] not in payload
