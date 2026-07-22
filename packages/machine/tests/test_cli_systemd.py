"""Tests for systemd unit PATH handling and runtime PATH augmentation.

Issue #545 — the generated systemd user unit pinned a minimal PATH
that excluded ~/.local/bin (claude/codex) and /usr/local/bin (gemini),
so engine auto-detection found 0 engines under systemd while a login
shell found all 3. These tests lock in the two fixes:

  * ``build_systemd_path`` bakes the install-time PATH + well-known
    user bin dirs into the unit (parity with the detecting shell).
  * ``ensure_engine_paths`` augments the live process PATH at daemon
    startup so already-deployed units self-heal and spawned agents
    inherit the fix.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from anygarden_machine.cli import (
    _dedup_path,
    _wellknown_engine_dirs,
    build_systemd_path,
    ensure_engine_paths,
    main,
)


class TestDedupPath:
    """``_dedup_path`` — order-preserving, empty-dropping dedup."""

    def test_preserves_order_and_dedups(self) -> None:
        assert _dedup_path(["/a", "/b", "/a", "/c"]) == "/a:/b:/c"

    def test_drops_empty_entries(self) -> None:
        assert _dedup_path(["", "/a", "", "/b"]) == "/a:/b"

    def test_empty_input(self) -> None:
        assert _dedup_path([]) == ""


class TestWellknownEngineDirs:
    """``_wellknown_engine_dirs`` — the two user install locations."""

    def test_includes_local_bin_and_usr_local_bin(self) -> None:
        dirs = {str(d) for d in _wellknown_engine_dirs()}
        assert str(Path.home() / ".local" / "bin") in dirs
        assert "/usr/local/bin" in dirs


class TestBuildSystemdPath:
    """``build_systemd_path`` — the PATH baked into the unit file."""

    def test_includes_wellknown_dirs(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        result = build_systemd_path()
        assert str(Path.home() / ".local" / "bin") in result.split(":")
        assert "/usr/local/bin" in result.split(":")

    def test_captures_install_time_path(self, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "/opt/nvm/bin:/usr/bin")
        result = build_systemd_path().split(":")
        # The shell PATH where the engines were detectable is preserved.
        assert "/opt/nvm/bin" in result

    def test_no_duplicates(self, monkeypatch) -> None:
        # /usr/local/bin appears both in the shell PATH and the
        # well-known list — it must show up exactly once.
        monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
        parts = build_systemd_path().split(":")
        assert parts.count("/usr/local/bin") == 1
        assert parts.count("/usr/bin") == 1


class TestEnsureEnginePaths:
    """``ensure_engine_paths`` — idempotent runtime PATH augmentation."""

    def test_appends_missing(self) -> None:
        env = {"PATH": "/usr/bin:/bin"}
        ensure_engine_paths(env)
        parts = env["PATH"].split(":")
        assert str(Path.home() / ".local" / "bin") in parts
        assert "/usr/local/bin" in parts

    def test_idempotent(self) -> None:
        env = {"PATH": "/usr/bin:/bin"}
        ensure_engine_paths(env)
        once = env["PATH"]
        ensure_engine_paths(env)
        assert env["PATH"] == once

    def test_preserves_existing_order(self) -> None:
        env = {"PATH": "/first:/second"}
        ensure_engine_paths(env)
        parts = env["PATH"].split(":")
        assert parts[0] == "/first"
        assert parts[1] == "/second"

    def test_no_duplicate_when_already_present(self) -> None:
        local_bin = str(Path.home() / ".local" / "bin")
        env = {"PATH": f"{local_bin}:/usr/bin"}
        ensure_engine_paths(env)
        assert env["PATH"].split(":").count(local_bin) == 1

    def test_empty_path(self) -> None:
        env: dict[str, str] = {}
        ensure_engine_paths(env)
        parts = env["PATH"].split(":")
        assert "/usr/local/bin" in parts


class TestInstallSystemdUnit:
    """``install-systemd-unit`` writes a unit with the user bin dirs."""

    def test_unit_path_line_includes_wellknown(self, monkeypatch, tmp_path) -> None:
        # Redirect Path.home() so both the unit output location and the
        # well-known ~/.local/bin resolve under tmp_path.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        result = CliRunner().invoke(main, ["install-systemd-unit"])
        assert result.exit_code == 0, result.output

        unit = (
            tmp_path / ".config" / "systemd" / "user" / "anygarden-machine.service"
        ).read_text()
        path_line = next(
            line for line in unit.splitlines() if line.startswith("Environment=PATH=")
        )
        assert str(tmp_path / ".local" / "bin") in path_line
        assert "/usr/local/bin" in path_line
