from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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
    assert payload["result_code"] == "PREFLIGHT_PASS"
    assert payload["exact_sha"] == env["GITHUB_SHA"]
    assert payload["input_sha256"] == smoke._sha256(smoke.CANARY_PROMPT.encode())
    assert credential not in evidence.read_text()
    assert smoke.CANARY_PROMPT not in evidence.read_text()


def test_live_runner_requires_credential_only_after_isolation(
    tmp_path: Path, monkeypatch
) -> None:
    env = configured_env()
    env.pop("OPENAI_API_KEY")
    env["ANYGARDEN_SMOKE_RUNTIME_ISOLATED"] = (
        "container-readonly-empty-workspace"
    )
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


def test_runtime_requires_isolated_empty_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(smoke.BlockedConfiguration):
        smoke.execute("gpt-5.4-mini", {"OPENAI_API_KEY": "x"})


@pytest.mark.parametrize(
    ("error", "exit_code", "result_code"),
    [
        (TimeoutError("raw response"), 124, "TIMEOUT"),
        (RuntimeError("/home/runner embedded-sensitive-value"), 1, "FAIL"),
    ],
)
def test_failure_evidence_never_contains_exception_text(
    tmp_path: Path,
    monkeypatch,
    error: Exception,
    exit_code: int,
    result_code: str,
) -> None:
    env = configured_env()
    evidence = tmp_path / "evidence.json"

    def fail(_model: str, _env: dict[str, str]) -> tuple[bytes, str]:
        raise error

    monkeypatch.setattr(smoke, "execute", fail)
    assert smoke.run("run", evidence, env) == exit_code
    payload = evidence.read_text()
    assert json.loads(payload)["result_code"] == result_code
    assert str(error) not in payload
    assert env["OPENAI_API_KEY"] not in payload
