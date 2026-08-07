"""Provider-free validation of the production smoke runner's pipe capture.

Run this inside the immutable engine-smoke image with Docker networking set to
``none``.  Each case invokes the normal :func:`engine_smoke_gate.run` entry
point while substituting only its child command with a deterministic local
Python process.  The production preflight, engine-version check, pipe drain,
timeout, exit projection, classification, and evidence writer remain intact.
The child writes no stdout, writes at most one fixed stderr fixture, and exits
nonzero.  Raw pipe contents are never printed or copied to evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import engine_smoke_gate as smoke

RUNNER_MODEL = "gpt-5.6-sol"
RUNNER_CREDENTIAL = "runner-fixture-only-not-a-credential"
RUNNER_PROXY = "http://192.168.100.97:3129"


@dataclass(frozen=True)
class RunnerCase:
    name: str
    stderr_fixture: str
    expected_category: str
    expected_stderr_state: str
    expected_failure_phase: str
    expected_allowlist_match: bool


@dataclass(frozen=True)
class RunnerObservation:
    case: str
    matched: bool
    allowlist_matched: bool
    result_code: str
    failure_category: str
    failure_phase: str
    engine_exit_state: str
    stdout_state: str
    stderr_state: str
    output_length: int
    raw_retained: bool


CASES = (
    RunnerCase(
        name="STDERR_AUTH_MATCH",
        stderr_fixture="authentication failed",
        expected_category="AUTH_REJECTED",
        expected_stderr_state="SINGLE_FAILURE_SIGNAL",
        expected_failure_phase="STDERR_CLASSIFICATION",
        expected_allowlist_match=True,
    ),
    RunnerCase(
        name="STDERR_MODEL_MATCH",
        stderr_fixture="model not found",
        expected_category="MODEL_ACCESS",
        expected_stderr_state="SINGLE_FAILURE_SIGNAL",
        expected_failure_phase="STDERR_CLASSIFICATION",
        expected_allowlist_match=True,
    ),
    RunnerCase(
        name="STDERR_UNRECOGNIZED",
        stderr_fixture="opaque deterministic failure",
        expected_category=smoke.FAILURE_CATEGORY_UNKNOWN,
        expected_stderr_state="UNRECOGNIZED",
        expected_failure_phase="STDERR_CLASSIFICATION",
        expected_allowlist_match=False,
    ),
    RunnerCase(
        name="NO_OUTPUT",
        stderr_fixture="",
        expected_category=smoke.FAILURE_CATEGORY_ENGINE_EMPTY_OUTPUT,
        expected_stderr_state="EMPTY",
        expected_failure_phase="ENGINE_EXIT",
        expected_allowlist_match=False,
    ),
)


def _assert_loopback_only() -> None:
    interfaces = {name for _index, name in socket.if_nameindex()}
    if interfaces != {"lo"}:
        raise RuntimeError("network_not_isolated")


def _configured_env(root: Path) -> dict[str, str]:
    sha = "a" * 40
    runtime_root = root / "runtime"
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": sha,
        "GITHUB_RUN_ID": "runner-fixture",
        "ANYGARDEN_DEFAULT_BRANCH": "main",
        "ANYGARDEN_CHECKOUT_SHA": sha,
        "ANYGARDEN_SMOKE_ENVIRONMENT_PROTECTED": "true",
        "ANYGARDEN_SMOKE_APPROVED": "true",
        "ANYGARDEN_SMOKE_BUDGET_POLICY": "vendor-daily-cap-1",
        "ANYGARDEN_SMOKE_EGRESS_POLICY": "vendor-only",
        "ANYGARDEN_SMOKE_PROXY_URL": RUNNER_PROXY,
        "HTTP_PROXY": RUNNER_PROXY,
        "HTTPS_PROXY": RUNNER_PROXY,
        "ANYGARDEN_SMOKE_CREDENTIAL_SCOPE": "low-privilege-test-only",
        "ANYGARDEN_SMOKE_CONTAINER_IMAGE": (
            "ghcr.io/e7217/anygarden-engine-smoke@sha256:" + "b" * 64
        ),
        "ANYGARDEN_SMOKE_MODEL": RUNNER_MODEL,
        "ANYGARDEN_SMOKE_RUNTIME_ISOLATED": ("container-readonly-empty-workspace"),
        "HOME": str(runtime_root / "home"),
        "CODEX_HOME": str(runtime_root / "codex"),
        "OPENAI_API_KEY": RUNNER_CREDENTIAL,
    }


def _build_fixture_command(stderr_fixture: str) -> list[str]:
    source = (
        "import sys\n"
        f"STDERR_FIXTURE = {stderr_fixture!r}\n"
        "sys.stdin.buffer.read()\n"
        "if STDERR_FIXTURE:\n"
        "    sys.stderr.write(STDERR_FIXTURE)\n"
        "raise SystemExit(1)\n"
    )
    return [sys.executable, "-c", source]


def run_case(case: RunnerCase) -> RunnerObservation:
    original_cwd = Path.cwd()
    original_runtime_root = smoke.RUNTIME_STATE_ROOT
    original_build_command = smoke.build_command
    with tempfile.TemporaryDirectory(prefix="anygarden-runner-") as temp_dir:
        root = Path(temp_dir)
        workspace = root / "workspace"
        workspace.mkdir(mode=0o700)
        env = _configured_env(root)
        evidence = root / "evidence.json"
        smoke.RUNTIME_STATE_ROOT = root / "runtime"

        def fixture_build_command(model: str) -> list[str]:
            del model
            return _build_fixture_command(case.stderr_fixture)

        smoke.build_command = fixture_build_command
        try:
            os.chdir(workspace)
            exit_code = smoke.run("run", evidence, env)
        finally:
            os.chdir(original_cwd)
            smoke.RUNTIME_STATE_ROOT = original_runtime_root
            smoke.build_command = original_build_command
        payload_text = evidence.read_text(encoding="utf-8")
        payload = json.loads(payload_text)
        raw_retained = any(
            value and value in payload_text
            for value in (
                case.stderr_fixture,
                RUNNER_CREDENTIAL,
                RUNNER_PROXY,
                RUNNER_MODEL,
            )
        )
        allowlist_matched = (
            payload["stderr_state"] == "SINGLE_FAILURE_SIGNAL"
            and payload["failure_category"] == case.expected_category
        )
        matched = (
            exit_code == 1
            and payload["result_code"] == "FAIL_ENGINE_NONZERO"
            and payload["failure_category"] == case.expected_category
            and payload["failure_phase"] == case.expected_failure_phase
            and payload["engine_exit_state"] == "NONZERO"
            and payload["stdout_state"] == "EMPTY"
            and payload["stderr_state"] == case.expected_stderr_state
            and payload["output_length"] == 0
            and allowlist_matched == case.expected_allowlist_match
            and not raw_retained
        )
        return RunnerObservation(
            case=case.name,
            matched=matched,
            allowlist_matched=allowlist_matched,
            result_code=str(payload["result_code"]),
            failure_category=str(payload["failure_category"]),
            failure_phase=str(payload["failure_phase"]),
            engine_exit_state=str(payload["engine_exit_state"]),
            stdout_state=str(payload["stdout_state"]),
            stderr_state=str(payload["stderr_state"]),
            output_length=int(payload["output_length"]),
            raw_retained=raw_retained,
        )


def _build_payload(observations: Sequence[RunnerObservation]) -> dict[str, object]:
    return {
        "network": "LOOPBACK_ONLY",
        "credential": "FIXTURE_ONLY",
        "provider_calls": 0,
        "all_matched": all(observation.matched for observation in observations),
        "cases": [asdict(observation) for observation in observations],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    _assert_loopback_only()
    observations = [run_case(case) for case in CASES]
    payload = _build_payload(observations)
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        for observation in observations:
            print(
                f"{observation.case}: matched={str(observation.matched).lower()} "
                f"category={observation.failure_category} "
                f"stdout={observation.stdout_state} "
                f"stderr={observation.stderr_state}"
            )
    return 0 if payload["all_matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
