"""Tests for engine CLI update execution (#553)."""

from __future__ import annotations

import subprocess

from anygarden_machine.engines.updater import run_engine_update


def _ok_runner(calls: list[list[str]]):
    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return runner


class TestRunEngineUpdate:
    def test_npm_engine_builds_correct_argv(self):
        calls: list[list[str]] = []
        result = run_engine_update("codex-cli", runner=_ok_runner(calls))
        assert result.ok
        assert result.error is None
        assert calls[0] == ["npm", "install", "-g", "@openai/codex@latest"]

    def test_pip_engine_uses_target_interpreter(self):
        calls: list[list[str]] = []
        result = run_engine_update(
            "openhands", python="/agent/venv/bin/python", runner=_ok_runner(calls)
        )
        assert result.ok
        assert calls[0] == [
            "/agent/venv/bin/python",
            "-m",
            "pip",
            "install",
            "-U",
            "openhands-sdk",
        ]

    def test_unknown_engine_is_rejected(self):
        def runner(cmd, **kwargs):
            raise AssertionError("runner must not run for a rejected engine")

        result = run_engine_update("evil-engine", runner=runner)
        assert not result.ok
        assert "unknown engine" in (result.error or "")

    def test_nonzero_exit_is_failure(self):
        def runner(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "npm ERR! boom")

        result = run_engine_update("gemini-cli", runner=runner)
        assert not result.ok
        assert "boom" in (result.error or "")

    def test_subprocess_error_is_captured_not_raised(self):
        def runner(cmd, **kwargs):
            raise OSError("no npm on PATH")

        result = run_engine_update("codex-cli", runner=runner)
        assert not result.ok
        assert "install failed" in (result.error or "")

    def test_pip_engine_without_interpreter_fails(self):
        # No sys.executable fallback: a pip engine without an explicit
        # interpreter is refused (else it installs into the wrong venv).
        def runner(cmd, **kwargs):
            raise AssertionError("must not run without an interpreter")

        result = run_engine_update("openhands", runner=runner)
        assert not result.ok
        assert "interpreter" in (result.error or "")
