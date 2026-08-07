from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).parents[1] / "scripts"

SMOKE_SPEC = importlib.util.spec_from_file_location(
    "engine_smoke_gate",
    SCRIPTS / "engine_smoke_gate.py",
)
assert SMOKE_SPEC is not None and SMOKE_SPEC.loader is not None
smoke = importlib.util.module_from_spec(SMOKE_SPEC)
sys.modules[SMOKE_SPEC.name] = smoke
SMOKE_SPEC.loader.exec_module(smoke)

RUNNER_SPEC = importlib.util.spec_from_file_location(
    "engine_smoke_runner_validation",
    SCRIPTS / "engine_smoke_runner_validation.py",
)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)


def _fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "codex"
    executable.write_text(
        f"#!{sys.executable}\nprint('codex-cli 0.146.0')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def test_exact_runner_reproduces_stdout_empty_stderr_only_nonzero(
    tmp_path: Path,
) -> None:
    fake_codex = _fake_codex(tmp_path)
    with patch.object(runner.smoke.shutil, "which", return_value=str(fake_codex)):
        observations = [runner.run_case(case) for case in runner.CASES]

    assert all(observation.matched for observation in observations)
    assert all(
        observation.result_code == "FAIL_ENGINE_NONZERO" for observation in observations
    )
    assert all(
        observation.engine_exit_state == "NONZERO" for observation in observations
    )
    assert all(observation.stdout_state == "EMPTY" for observation in observations)
    assert all(observation.output_length == 0 for observation in observations)
    assert all(not observation.raw_retained for observation in observations)


def test_runner_keeps_allowlist_match_distinct_from_closed_fallback(
    tmp_path: Path,
) -> None:
    fake_codex = _fake_codex(tmp_path)
    with patch.object(runner.smoke.shutil, "which", return_value=str(fake_codex)):
        by_case = {case.name: runner.run_case(case) for case in runner.CASES}

    assert by_case["STDERR_AUTH_MATCH"].allowlist_matched is True
    assert by_case["STDERR_AUTH_MATCH"].failure_category == "AUTH_REJECTED"
    assert by_case["STDERR_AUTH_MATCH"].stderr_state == "SINGLE_FAILURE_SIGNAL"
    assert by_case["STDERR_MODEL_MATCH"].allowlist_matched is True
    assert by_case["STDERR_MODEL_MATCH"].failure_category == "MODEL_ACCESS"
    assert by_case["STDERR_UNRECOGNIZED"].allowlist_matched is False
    assert by_case["STDERR_UNRECOGNIZED"].failure_category == "UNKNOWN"
    assert by_case["STDERR_UNRECOGNIZED"].stderr_state == "UNRECOGNIZED"
    assert by_case["NO_OUTPUT"].allowlist_matched is False
    assert by_case["NO_OUTPUT"].failure_category == "ENGINE_EMPTY_OUTPUT"
    assert by_case["NO_OUTPUT"].stderr_state == "EMPTY"


def test_runner_child_is_local_and_deterministic() -> None:
    command = runner._build_fixture_command("model not found")

    assert command[0] == sys.executable
    assert command[1] == "-c"
    assert "sys.stdin.buffer.read()" in command[2]
    assert "sys.stderr.write(STDERR_FIXTURE)" in command[2]
