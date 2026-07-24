"""Tests for install-method detection (#556).

``resolve_install`` picks the update strategy; its branch logic is tested
by stubbing the detection helpers, and the helpers are tested directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from anygarden_machine import install_detect
from anygarden_machine.install_detect import (
    MACHINE_PACKAGE,
    METHOD_PIP_UMBRELLA,
    METHOD_UV_TOOL,
    METHOD_VENV_PIP,
    UMBRELLA_PACKAGE,
    ResolvedInstall,
    resolve_install,
)
from anygarden_machine.install_manifest import InstallManifest

# ── resolve_install: manifest-first ───────────────────────────────────


def test_manifest_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even if detection would say "uv tool", a manifest wins verbatim.
    monkeypatch.setattr(install_detect, "_is_uv_tool_install", lambda _p: True)
    m = InstallManifest(
        method="venv-pip",
        package="anygarden-machine",
        python="/owned/venv/bin/python",
        index_url="https://pypi.example.com/simple",
    )
    r = resolve_install(m)
    assert r == ResolvedInstall(
        method="venv-pip",
        python="/owned/venv/bin/python",
        package="anygarden-machine",
        index_url="https://pypi.example.com/simple",
    )


# ── resolve_install: detection fallback ───────────────────────────────


def test_detect_uv_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detect, "_is_uv_tool_install", lambda _p: True)
    r = resolve_install(None)
    assert r.method == METHOD_UV_TOOL
    assert r.package == UMBRELLA_PACKAGE


def test_uv_tool_wins_over_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    # A uv venv with pip smuggled in (ensurepip) must still resolve to uv.
    monkeypatch.setattr(install_detect, "_is_uv_tool_install", lambda _p: True)
    monkeypatch.setattr(install_detect, "_has_pip", lambda: True)
    monkeypatch.setattr(install_detect, "_has_distribution", lambda _n: True)
    assert resolve_install(None).method == METHOD_UV_TOOL


def test_detect_pip_umbrella(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detect, "_is_uv_tool_install", lambda _p: False)
    monkeypatch.setattr(install_detect, "_has_pip", lambda: True)
    monkeypatch.setattr(
        install_detect, "_has_distribution", lambda n: n == UMBRELLA_PACKAGE
    )
    r = resolve_install(None)
    assert r.method == METHOD_PIP_UMBRELLA
    assert r.package == UMBRELLA_PACKAGE


def test_detect_venv_pip_standalone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detect, "_is_uv_tool_install", lambda _p: False)
    monkeypatch.setattr(install_detect, "_has_pip", lambda: True)
    monkeypatch.setattr(install_detect, "_has_distribution", lambda _n: False)
    r = resolve_install(None)
    assert r.method == METHOD_VENV_PIP
    assert r.package == MACHINE_PACKAGE


def test_no_supported_method_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detect, "_is_uv_tool_install", lambda _p: False)
    monkeypatch.setattr(install_detect, "_has_pip", lambda: False)
    with pytest.raises(ValueError):
        resolve_install(None)


# ── _uv_tool_root: resolution order ───────────────────────────────────


def test_uv_tool_root_env_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path))
    assert install_detect._uv_tool_root() == tmp_path


def test_uv_tool_root_uses_uv_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr(
        install_detect.shutil, "which", lambda n: "/usr/bin/uv" if n == "uv" else None
    )

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{tmp_path}\n", stderr="")

    monkeypatch.setattr(install_detect.subprocess, "run", fake_run)
    assert install_detect._uv_tool_root() == tmp_path


def test_uv_tool_root_default_when_no_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setattr(install_detect.shutil, "which", lambda _n: None)
    assert (
        install_detect._uv_tool_root()
        == Path.home() / ".local" / "share" / "uv" / "tools"
    )


# ── _is_uv_tool_install ───────────────────────────────────────────────


def test_is_uv_tool_install_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_detect, "_uv_tool_root", lambda: tmp_path)
    python = tmp_path / "anygarden" / "bin" / "python"
    assert install_detect._is_uv_tool_install(str(python)) is True


def test_is_uv_tool_install_false_outside_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install_detect, "_uv_tool_root", lambda: tmp_path)
    assert install_detect._is_uv_tool_install("/usr/bin/python") is False


def test_is_uv_tool_install_false_no_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(install_detect, "_uv_tool_root", lambda: None)
    assert install_detect._is_uv_tool_install("/whatever/python") is False


# ── _has_pip / _has_distribution (light sanity) ───────────────────────


def test_has_distribution_true_for_self() -> None:
    # anygarden-machine is installed in the test environment.
    assert install_detect._has_distribution("anygarden-machine") is True


def test_has_distribution_false_for_unknown() -> None:
    assert install_detect._has_distribution("no-such-dist-xyz") is False
