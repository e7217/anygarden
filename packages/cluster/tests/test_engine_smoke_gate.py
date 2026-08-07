from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from collections.abc import Callable
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


def popen_with_stderr(process: object, stderr: bytes) -> Callable[..., object]:
    def factory(*_args: object, **kwargs: object) -> object:
        if stderr:
            stderr_fd = kwargs["stderr"]
            assert isinstance(stderr_fd, int)
            writer_fd = os.dup(stderr_fd)

            def write_stderr() -> None:
                remaining = memoryview(stderr)
                try:
                    while remaining:
                        remaining = remaining[os.write(writer_fd, remaining) :]
                finally:
                    os.close(writer_fd)

            threading.Thread(target=write_stderr, daemon=True).start()
        return process

    return factory


def test_missing_configuration_is_blocked_and_redacted(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"

    assert smoke.run("preflight", evidence, {}) == 2

    payload = evidence.read_text()
    decoded = json.loads(payload)
    assert decoded["result_code"] == "BLOCKED_CONFIGURATION"
    assert decoded["failure_phase"] == "ENGINE_LAUNCH"
    assert decoded["engine_exit_state"] == "NOT_OBSERVED"
    assert decoded["stdout_state"] == "NOT_OBSERVED"
    assert decoded["stderr_state"] == "NOT_OBSERVED"
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
        "classification_ms",
        "duration_ms",
        "engine",
        "engine_exit_state",
        "engine_ms",
        "engine_version",
        "exact_sha",
        "failure_category",
        "failure_phase",
        "input_sha256",
        "model_version",
        "output_length",
        "output_sha256",
        "result_code",
        "setup_ms",
        "stderr_state",
        "stdout_state",
        "workflow_run",
    }
    assert payload["result_code"] == "PREFLIGHT_PASS"
    assert payload["failure_category"] == "NOT_APPLICABLE"
    assert payload["failure_phase"] == "ENGINE_LAUNCH"
    assert payload["engine_exit_state"] == "NOT_OBSERVED"
    assert payload["stdout_state"] == "NOT_OBSERVED"
    assert payload["stderr_state"] == "NOT_OBSERVED"
    assert payload["model_version"] == ""
    assert payload["exact_sha"] == env["GITHUB_SHA"]
    assert payload["input_sha256"] == smoke._sha256(smoke.CANARY_PROMPT.encode())
    assert credential not in evidence.read_text()
    assert env["ANYGARDEN_SMOKE_MODEL"] not in evidence.read_text()
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


@pytest.mark.parametrize(
    ("returncode", "state"),
    [(0, "ZERO"), (1, "NONZERO"), (-9, "SIGNAL"), (None, "NOT_OBSERVED")],
)
def test_raw_exit_code_projects_to_fixed_state(
    returncode: int | None, state: str
) -> None:
    assert smoke._engine_exit_state(returncode) == state
    assert state in smoke.ENGINE_EXIT_STATES


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
        (b"", "ENGINE_EMPTY_OUTPUT"),
        (
            (
                b'{"type":"error","message":"unexpected status 401 '
                b'Unauthorized: provider-secret, url: https://example.invalid"}'
            ),
            "AUTH_REJECTED",
        ),
        (
            (
                b'{"type":"error","message":"unexpected status 401 '
                b'Unauthorized: Incorrect API key provided"}'
            ),
            "AUTH_REJECTED",
        ),
        (
            (
                b'{"type":"turn.failed","error":{"message":"unexpected '
                b"status 403 Forbidden: The requested model does not exist or "
                b'you do not have access to it"}}'
            ),
            "MODEL_ACCESS",
        ),
        (
            (
                b'{"type":"turn.failed","error":{"message":"unexpected '
                b'status 404 Not Found: Model not found provider-secret"}}'
            ),
            "MODEL_ACCESS",
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
            "AUTH_REJECTED",
        ),
        (
            (
                b'{"type":"item.completed","item":{"type":"agent_message",'
                b'"text":"unexpected status 401 Unauthorized"}}'
            ),
            "UNKNOWN",
        ),
    ],
    ids=(
        "empty",
        "auth-status",
        "auth-status-and-copy",
        "model-access-403",
        "model-access",
        "rate-status",
        "upstream-status",
        "rate-copy",
        "upstream-copy",
        "status-precedence",
        "auth-precedence",
        "non-failure-event",
    ),
)
def test_failure_classifier_projects_only_closed_categories(
    raw: bytes, category: str
) -> None:
    assert smoke.classify_failure(raw) == category
    assert category in smoke.FAILURE_CATEGORIES


@pytest.mark.parametrize(
    ("raw", "category", "stdout_state"),
    [
        (b"", "ENGINE_EMPTY_OUTPUT", "EMPTY"),
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized"}',
            "AUTH_REJECTED",
            "SINGLE_FAILURE_EVENT",
        ),
        (
            (
                b'{"type":"error","message":"unexpected status 429 Too Many '
                b'Requests"}\n'
                b'{"type":"error","message":"unexpected status 429 Too Many '
                b'Requests"}'
            ),
            "UNKNOWN",
            "MULTIPLE_FAILURE_EVENTS",
        ),
        (b"not-json", "UNKNOWN", "MALFORMED"),
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized"}'
            + b" " * (smoke.MAX_FAILURE_EVENT_BYTES + 1),
            "UNKNOWN",
            "OVERSIZE",
        ),
        (
            b'{"type":"thread.started","thread_id":"opaque"}',
            "UNKNOWN",
            "NON_FAILURE_OUTPUT",
        ),
    ],
)
def test_failure_classifier_records_only_closed_stdout_state(
    raw: bytes, category: str, stdout_state: str
) -> None:
    assert smoke.classify_failure_observation(raw) == (category, stdout_state)
    assert stdout_state in smoke.STDOUT_STATES


@pytest.mark.parametrize(
    ("raw", "category", "stderr_state"),
    [
        (b"", "ENGINE_EMPTY_OUTPUT", "EMPTY"),
        (b"authentication failed", "AUTH_REJECTED", "SINGLE_FAILURE_SIGNAL"),
        (b"model not found", "MODEL_ACCESS", "SINGLE_FAILURE_SIGNAL"),
        (b"invalid reasoning effort", "ENGINE_CONFIG", "SINGLE_FAILURE_SIGNAL"),
        (b"connection failed:", "UPSTREAM", "SINGLE_FAILURE_SIGNAL"),
        (
            b"authentication failed\nmodel not found",
            "UNKNOWN",
            "MULTIPLE_FAILURE_SIGNALS",
        ),
        (
            b"authentication failed\nauthentication failed",
            "UNKNOWN",
            "MULTIPLE_FAILURE_SIGNALS",
        ),
        (
            b"authentication failed authentication failed",
            "UNKNOWN",
            "MULTIPLE_FAILURE_SIGNALS",
        ),
        (
            b"unexpected status 401 Unauthorized: unexpected status 401 Unauthorized",
            "UNKNOWN",
            "MULTIPLE_FAILURE_SIGNALS",
        ),
        (
            b"x" * (smoke.MAX_FAILURE_EVENT_BYTES + 1),
            "UNKNOWN",
            "OVERSIZE",
        ),
        (b"\xff", "UNKNOWN", "MALFORMED"),
        (b"opaque failure", "UNKNOWN", "UNRECOGNIZED"),
    ],
    ids=(
        "empty",
        "auth",
        "model-access",
        "engine-config",
        "upstream",
        "conflicting-signals",
        "repeated-signal",
        "same-line-repeated-signal",
        "same-line-repeated-status",
        "oversize",
        "malformed",
        "unrecognized",
    ),
)
def test_stderr_classifier_adopts_exactly_one_bounded_allowlist_signal(
    raw: bytes, category: str, stderr_state: str
) -> None:
    assert smoke.classify_stderr_observation(raw) == (category, stderr_state)
    assert category in smoke.FAILURE_CATEGORIES
    assert stderr_state in smoke.STDERR_STATES


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
            b'{"type":"error","message":"unexpected status 429 Too Many '
            b'Requests"}\n'
            b'{"type":"error","message":"unexpected status 429 Too Many '
            b'Requests"}'
        ),
        (
            b'{"type":"turn.failed","error":{"message":"unexpected status '
            b'401 Unauthorized"}}\n'
            b'{"type":"turn.failed","error":{"message":"unexpected status '
            b'401 Unauthorized"}}'
        ),
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized"}'
            + b" " * (smoke.MAX_FAILURE_EVENT_BYTES + 1)
        ),
        (b'{"type":"error","message":"authentication failed authentication failed"}'),
    ],
    ids=(
        "non-json",
        "non-string-message",
        "invalid-error-shape",
        "unrecognized",
        "message-oversize",
        "conflicting-events",
        "repeated-rate-events",
        "repeated-terminal-events",
        "event-oversize",
        "same-line-repeated-signal",
    ),
)
def test_failure_classifier_collapses_unsafe_input_to_unknown(raw: bytes) -> None:
    assert smoke.classify_failure(raw) == "UNKNOWN"


@pytest.mark.parametrize(
    "raw",
    [
        (
            b'{"type":"error","message":"unexpected status 401 Unauthorized"}\n'
            b'{"type":"turn.failed","error":{"message":"unexpected status '
            b'401 Unauthorized"}}'
        ),
        (
            b'{"type":"error","message":"unrecognized retry diagnostic"}\n'
            b'{"type":"turn.failed","error":{"message":"unexpected status '
            b'401 Unauthorized"}}'
        ),
    ],
    ids=("same-category-retry", "unrecognized-retry"),
)
def test_failure_classifier_uses_one_authoritative_terminal_event(
    raw: bytes,
) -> None:
    assert smoke.classify_failure_observation(raw) == (
        "AUTH_REJECTED",
        "TERMINAL_FAILURE",
    )


def test_evidence_boundary_collapses_unlisted_category_without_leaking() -> None:
    sensitive_category = "AUTH_REJECTED:raw-provider-secret"

    evidence = smoke.make_evidence(
        configured_env(),
        "FAIL_ENGINE_NONZERO",
        0,
        engine_version="codex-cli raw-provider-secret",
        failure_category=sensitive_category,
        diagnostics=smoke.ExecutionDiagnostics(
            failure_phase="ENGINE_EXIT:raw-provider-secret",
            engine_exit_state="137",
            stdout_state="raw-provider-secret",
            setup_ms=-1,
            engine_ms=True,
            classification_ms=smoke.MAX_EVIDENCE_DURATION_MS + 1,
        ),
    )

    assert evidence.failure_category == "UNKNOWN"
    assert evidence.engine_version == ""
    assert evidence.failure_phase == "ENGINE_LAUNCH"
    assert evidence.engine_exit_state == "NOT_OBSERVED"
    assert evidence.stdout_state == "NOT_OBSERVED"
    assert evidence.stderr_state == "NOT_OBSERVED"
    assert evidence.setup_ms == 0
    assert evidence.engine_ms == 0
    assert evidence.classification_ms == smoke.MAX_EVIDENCE_DURATION_MS
    assert sensitive_category not in json.dumps(evidence.__dict__)
    assert "raw-provider-secret" not in json.dumps(evidence.__dict__)


@pytest.mark.parametrize(
    "diagnostics",
    [
        smoke.ExecutionDiagnostics(
            failure_phase="ENGINE_EXIT",
            engine_exit_state="ZERO",
            stdout_state="EMPTY",
        ),
        smoke.ExecutionDiagnostics(
            failure_phase="ENGINE_EXIT",
            engine_exit_state="SIGNAL",
            stdout_state="EMPTY",
        ),
        smoke.ExecutionDiagnostics(
            failure_phase="ENGINE_EXIT",
            engine_exit_state="NONZERO",
            stdout_state="NON_FAILURE_OUTPUT",
            stderr_state="EMPTY",
        ),
        smoke.ExecutionDiagnostics(),
    ],
    ids=(
        "zero-exit",
        "signal-exit",
        "nonempty-stdout",
        "not-observed",
    ),
)
def test_engine_empty_output_requires_nonzero_and_zero_byte_stdout(
    diagnostics: object,
) -> None:
    evidence = smoke.make_evidence(
        configured_env(),
        "FAIL_ENGINE_NONZERO",
        0,
        failure_category="ENGINE_EMPTY_OUTPUT",
        diagnostics=diagnostics,
    )

    assert evidence.failure_category == "UNKNOWN"


@pytest.mark.parametrize(
    (
        "stdout",
        "stderr",
        "category",
        "stdout_state",
        "stderr_state",
        "failure_phase",
    ),
    [
        (
            (
                b'{"type":"turn.failed","error":{"message":"unexpected '
                b'status 401 Unauthorized: raw-provider-secret"}}'
            ),
            b"raw-stderr-secret",
            "AUTH_REJECTED",
            "SINGLE_FAILURE_EVENT",
            "UNRECOGNIZED",
            "STDOUT_CLASSIFICATION",
        ),
        (
            b'{"type":"turn.failed","error":{"message":"unknown raw failure"}}',
            b"unexpected status 401 Unauthorized: stderr-only-secret",
            "UNKNOWN",
            "SINGLE_FAILURE_EVENT",
            "SINGLE_FAILURE_SIGNAL",
            "STDOUT_CLASSIFICATION",
        ),
        (
            b"",
            b"unexpected status 401 Unauthorized: stderr-only-secret",
            "AUTH_REJECTED",
            "EMPTY",
            "SINGLE_FAILURE_SIGNAL",
            "STDERR_CLASSIFICATION",
        ),
        (
            b"",
            b"invalid reasoning effort",
            "ENGINE_CONFIG",
            "EMPTY",
            "SINGLE_FAILURE_SIGNAL",
            "STDERR_CLASSIFICATION",
        ),
        (
            b"",
            b"authentication failed\nmodel not found",
            "UNKNOWN",
            "EMPTY",
            "MULTIPLE_FAILURE_SIGNALS",
            "STDERR_CLASSIFICATION",
        ),
        (
            b"",
            b"authentication failed authentication failed",
            "UNKNOWN",
            "EMPTY",
            "MULTIPLE_FAILURE_SIGNALS",
            "STDERR_CLASSIFICATION",
        ),
        (
            b"",
            b"x" * (smoke.MAX_FAILURE_EVENT_BYTES + 1),
            "UNKNOWN",
            "EMPTY",
            "OVERSIZE",
            "STDERR_CLASSIFICATION",
        ),
        (
            b"",
            b"opaque failure",
            "UNKNOWN",
            "EMPTY",
            "UNRECOGNIZED",
            "STDERR_CLASSIFICATION",
        ),
    ],
    ids=(
        "stdout-category-wins",
        "unknown-stdout-fails-closed",
        "stderr-auth",
        "stderr-config",
        "stderr-multiple",
        "stderr-same-line-repeat",
        "stderr-oversize",
        "stderr-unrecognized",
    ),
)
def test_nonzero_engine_evidence_keeps_only_projected_category(
    tmp_path: Path,
    monkeypatch,
    stdout: bytes,
    stderr: bytes,
    category: str,
    stdout_state: str,
    stderr_state: str,
    failure_phase: str,
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
            return stdout, None

    monkeypatch.setattr(
        smoke.subprocess, "Popen", popen_with_stderr(FakeProcess(), stderr)
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
    assert decoded["failure_phase"] == failure_phase
    assert decoded["engine_exit_state"] == "NONZERO"
    assert decoded["stdout_state"] == stdout_state
    assert decoded["stderr_state"] == stderr_state
    assert decoded["engine_version"] == "codex-cli 0.146.0"
    if stdout:
        assert stdout.decode() not in payload
    assert stderr.decode() not in payload
    assert "raw-provider-secret" not in payload
    assert "raw-stderr-secret" not in payload
    assert "stderr-only-secret" not in payload
    assert env["ANYGARDEN_SMOKE_MODEL"] not in payload


def test_provider_free_fake_engine_reproduces_empty_stdout_nonzero(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"codex-cli 0.146.0"
        ),
    )

    class FakeEngine:
        returncode = 1

        def communicate(self, **_kwargs):
            return b"", None

    monkeypatch.setattr(smoke.subprocess, "Popen", popen_with_stderr(FakeEngine(), b""))
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
    assert decoded["failure_category"] == "ENGINE_EMPTY_OUTPUT"
    assert decoded["failure_phase"] == "ENGINE_EXIT"
    assert decoded["engine_exit_state"] == "NONZERO"
    assert decoded["stdout_state"] == "EMPTY"
    assert decoded["stderr_state"] == "EMPTY"
    assert decoded["engine_version"] == "codex-cli 0.146.0"
    assert decoded["output_length"] == 0
    assert decoded["output_sha256"] == ""
    assert decoded["setup_ms"] >= 0
    assert decoded["engine_ms"] >= 0
    assert decoded["classification_ms"] >= 0
    assert env["OPENAI_API_KEY"] not in payload
    assert env["ANYGARDEN_SMOKE_PROXY_URL"] not in payload


def test_engine_version_must_match_fixed_non_sensitive_shape(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"codex-cli sensitive-version-secret"
        ),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("engine must not start")

    monkeypatch.setattr(smoke.subprocess, "Popen", forbidden)
    env = configured_env()
    env.update(
        {
            "ANYGARDEN_SMOKE_RUNTIME_ISOLATED": ("container-readonly-empty-workspace"),
            "HOME": str(runtime_root / "home"),
            "CODEX_HOME": str(runtime_root / "codex"),
        }
    )
    evidence = tmp_path / "evidence.json"

    assert smoke.run("run", evidence, env) == 2

    payload = evidence.read_text()
    assert json.loads(payload)["result_code"] == "BLOCKED_CONFIGURATION"
    assert "sensitive-version-secret" not in payload


def test_provider_free_success_keeps_closed_observability_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"codex-cli 0.146.0"
        ),
    )

    class FakeEngine:
        returncode = 0

        def communicate(self, **_kwargs):
            return (
                (
                    b'{"type":"item.completed","item":{"type":"agent_message",'
                    b'"text":"ANYGARDEN_SMOKE_OK"}}'
                ),
                None,
            )

    monkeypatch.setattr(
        smoke.subprocess,
        "Popen",
        popen_with_stderr(FakeEngine(), b"sensitive-success-stderr"),
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

    assert smoke.run("run", evidence, env) == 0

    payload = evidence.read_text()
    decoded = json.loads(payload)
    assert decoded["result_code"] == "PASS"
    assert decoded["failure_category"] == "NOT_APPLICABLE"
    assert decoded["failure_phase"] == "RESPONSE_PARSE"
    assert decoded["engine_exit_state"] == "ZERO"
    assert decoded["stdout_state"] == "NON_FAILURE_OUTPUT"
    assert decoded["stderr_state"] == "NOT_OBSERVED"
    assert decoded["output_length"] == len(smoke.CANARY_RESPONSE)
    assert decoded["output_sha256"] == smoke._sha256(smoke.CANARY_RESPONSE.encode())
    assert "sensitive-success-stderr" not in payload


def test_engine_launch_failure_records_only_fixed_state(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"codex-cli 0.146.0"
        ),
    )

    def fail_launch(*_args, **_kwargs):
        raise OSError("sensitive-launch-exception")

    monkeypatch.setattr(smoke.subprocess, "Popen", fail_launch)
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
    assert decoded["result_code"] == "FAIL"
    assert decoded["failure_category"] == "NOT_APPLICABLE"
    assert decoded["failure_phase"] == "ENGINE_LAUNCH"
    assert decoded["engine_exit_state"] == "NOT_OBSERVED"
    assert decoded["stdout_state"] == "NOT_OBSERVED"
    assert decoded["stderr_state"] == "NOT_OBSERVED"
    assert "sensitive-launch-exception" not in payload


def test_engine_timeout_records_only_fixed_state(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(smoke, "RUNTIME_STATE_ROOT", runtime_root)
    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/fake/codex")
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout=b"codex-cli 0.146.0"
        ),
    )
    monkeypatch.setattr(smoke, "_terminate_group", lambda _proc: None)

    class TimeoutEngine:
        returncode = None

        def communicate(self, **_kwargs):
            raise smoke.subprocess.TimeoutExpired(
                "sensitive-command", smoke.HARD_TIMEOUT_SECONDS
            )

    monkeypatch.setattr(
        smoke.subprocess, "Popen", popen_with_stderr(TimeoutEngine(), b"")
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

    assert smoke.run("run", evidence, env) == 124

    payload = evidence.read_text()
    decoded = json.loads(payload)
    assert decoded["result_code"] == "TIMEOUT"
    assert decoded["failure_category"] == "NOT_APPLICABLE"
    assert decoded["failure_phase"] == "ENGINE_EXECUTION"
    assert decoded["engine_exit_state"] == "SIGNAL"
    assert decoded["stdout_state"] == "NOT_OBSERVED"
    assert decoded["stderr_state"] == "NOT_OBSERVED"
    assert "sensitive-command" not in payload


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
