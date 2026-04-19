"""Tests for ``doorae_agent.secrets`` — private engine-secret storage.

These tests verify that:

* ``load_from_stdin`` correctly parses JSON payloads piped by the
  machine daemon and is robust against malformed/empty input.
* Stored secrets never land in ``os.environ`` by virtue of being read.
* ``env_with_secrets`` produces a clean env dict for subprocess
  spawns without mutating the parent process's environment.
* ``secrets_in_env`` temporarily populates ``os.environ`` for
  in-process SDKs and fully restores the prior state on exit
  (both "was absent" and "had prior value" cases).
"""

from __future__ import annotations

import io
import json
import os

import pytest

from doorae_agent import secrets


@pytest.fixture(autouse=True)
def _reset_secrets() -> None:
    secrets.clear()
    yield
    secrets.clear()


# ── load_from_stdin ───────────────────────────────────────────────────


class TestLoadFromStdin:
    def test_reads_json_object(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = json.dumps({"GEMINI_API_KEY": "sk-xyz", "OTHER": "v"})
        stream = io.StringIO(payload)
        # Mark as non-tty so ``isatty`` guard doesn't skip.
        stream.isatty = lambda: False  # type: ignore[assignment]
        monkeypatch.setattr("sys.stdin", stream)

        secrets.load_from_stdin()

        assert secrets.get("GEMINI_API_KEY") == "sk-xyz"
        assert secrets.get("OTHER") == "v"
        assert secrets.all_secrets() == {
            "GEMINI_API_KEY": "sk-xyz",
            "OTHER": "v",
        }

    def test_no_secrets_written_to_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Baseline property: simply loading secrets must not pollute
        ``os.environ``. /proc/self/environ for the agent process stays
        clean and an LLM tool call cannot read the keys via ``env`` or
        ``cat /proc/self/environ``.
        """
        payload = json.dumps({"ANTHROPIC_API_KEY": "sk-shh"})
        stream = io.StringIO(payload)
        stream.isatty = lambda: False  # type: ignore[assignment]
        monkeypatch.setattr("sys.stdin", stream)
        # Ensure a clean slate on the env var under test.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        secrets.load_from_stdin()

        assert "ANTHROPIC_API_KEY" not in os.environ
        assert secrets.get("ANTHROPIC_API_KEY") == "sk-shh"

    def test_empty_stdin_leaves_state_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = io.StringIO("")
        stream.isatty = lambda: False  # type: ignore[assignment]
        monkeypatch.setattr("sys.stdin", stream)
        secrets.load_from_stdin()
        assert secrets.all_secrets() == {}

    def test_malformed_json_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = io.StringIO("not-json")
        stream.isatty = lambda: False  # type: ignore[assignment]
        monkeypatch.setattr("sys.stdin", stream)
        secrets.load_from_stdin()
        assert secrets.all_secrets() == {}

    def test_non_dict_json_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = io.StringIO(json.dumps(["list", "not", "dict"]))
        stream.isatty = lambda: False  # type: ignore[assignment]
        monkeypatch.setattr("sys.stdin", stream)
        secrets.load_from_stdin()
        assert secrets.all_secrets() == {}

    def test_interactive_stdin_short_circuits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dev run with a terminal stdin must not block on read. The
        ``isatty`` guard causes ``load_from_stdin`` to return without
        touching stdin, so ``all_secrets`` stays empty.
        """
        stream = io.StringIO("should-never-be-read")
        stream.isatty = lambda: True  # type: ignore[assignment]
        monkeypatch.setattr("sys.stdin", stream)
        secrets.load_from_stdin()
        assert secrets.all_secrets() == {}


# ── env_with_secrets ──────────────────────────────────────────────────


class TestEnvWithSecrets:
    def test_merges_all_when_keys_none(self) -> None:
        secrets.set_secrets({"A": "1", "B": "2"})
        env = env = secrets.env_with_secrets({"PATH": "/bin"})
        assert env == {"PATH": "/bin", "A": "1", "B": "2"}

    def test_merges_only_requested_keys(self) -> None:
        secrets.set_secrets({"A": "1", "B": "2", "C": "3"})
        env = secrets.env_with_secrets({"PATH": "/bin"}, keys=["A", "C"])
        assert env == {"PATH": "/bin", "A": "1", "C": "3"}

    def test_ignores_missing_keys(self) -> None:
        secrets.set_secrets({"A": "1"})
        env = secrets.env_with_secrets({"PATH": "/bin"}, keys=["A", "B"])
        assert env == {"PATH": "/bin", "A": "1"}

    def test_defaults_base_env_to_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EXISTING", "v")
        secrets.set_secrets({"NEW": "n"})
        env = secrets.env_with_secrets()
        assert env["EXISTING"] == "v"
        assert env["NEW"] == "n"

    def test_does_not_mutate_base(self) -> None:
        secrets.set_secrets({"A": "1"})
        base = {"PATH": "/bin"}
        secrets.env_with_secrets(base)
        # Base dict is untouched
        assert base == {"PATH": "/bin"}

    def test_does_not_mutate_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SECRET_A", raising=False)
        secrets.set_secrets({"SECRET_A": "value"})
        secrets.env_with_secrets()
        assert "SECRET_A" not in os.environ


# ── secrets_in_env ────────────────────────────────────────────────────


class TestSecretsInEnv:
    def test_restores_absent_key_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SECRET_X", raising=False)
        secrets.set_secrets({"SECRET_X": "val"})

        with secrets.secrets_in_env(["SECRET_X"]):
            assert os.environ["SECRET_X"] == "val"

        assert "SECRET_X" not in os.environ

    def test_restores_prior_value_on_exit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECRET_X", "original")
        secrets.set_secrets({"SECRET_X": "override"})

        with secrets.secrets_in_env(["SECRET_X"]):
            assert os.environ["SECRET_X"] == "override"

        assert os.environ["SECRET_X"] == "original"

    def test_skips_unknown_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UNKNOWN", raising=False)
        secrets.set_secrets({})

        with secrets.secrets_in_env(["UNKNOWN"]):
            assert "UNKNOWN" not in os.environ

        assert "UNKNOWN" not in os.environ

    def test_restores_after_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exception inside the block must not leak the temporary env."""
        monkeypatch.delenv("SECRET_X", raising=False)
        secrets.set_secrets({"SECRET_X": "val"})

        with pytest.raises(RuntimeError):
            with secrets.secrets_in_env(["SECRET_X"]):
                assert os.environ["SECRET_X"] == "val"
                raise RuntimeError("boom")

        assert "SECRET_X" not in os.environ
