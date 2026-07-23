"""Tests for the self-owned install manifest (#550).

The bootstrap installer writes ``~/.anygarden/machine/install.json`` so
``anygarden-machine update`` can reinstall deterministically without
guessing the package manager. ``load`` must degrade to ``None`` for any
malformed/absent manifest so a non-bootstrap install falls back cleanly.
"""

from __future__ import annotations

import json

from anygarden_machine import install_manifest
from anygarden_machine.install_manifest import InstallManifest


def test_paths_live_under_anygarden_machine() -> None:
    assert install_manifest.INSTALL_ROOT.name == "machine"
    assert install_manifest.INSTALL_ROOT.parent.name == ".anygarden"
    assert install_manifest.MANIFEST_PATH.name == "install.json"
    assert install_manifest.VENV_DIR.parent == install_manifest.INSTALL_ROOT


def test_write_then_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "install.json"
    m = InstallManifest(
        method="venv-pip",
        package="anygarden-machine",
        python="/home/x/.anygarden/machine/venv/bin/python",
    )
    install_manifest.write(m, path=path)
    loaded = install_manifest.load(path=path)
    assert loaded == m
    assert loaded.index_url is None


def test_write_creates_parent_dirs(tmp_path) -> None:
    path = tmp_path / "nested" / "dir" / "install.json"
    install_manifest.write(
        InstallManifest(method="venv-pip", package="anygarden-machine", python="/p"),
        path=path,
    )
    assert path.exists()


def test_load_missing_returns_none(tmp_path) -> None:
    assert install_manifest.load(path=tmp_path / "nope.json") is None


def test_load_malformed_json_returns_none(tmp_path) -> None:
    path = tmp_path / "install.json"
    path.write_text("{not valid json")
    assert install_manifest.load(path=path) is None


def test_load_missing_field_returns_none(tmp_path) -> None:
    path = tmp_path / "install.json"
    path.write_text(json.dumps({"method": "venv-pip"}))  # no package/python
    assert install_manifest.load(path=path) is None


def test_index_url_preserved(tmp_path) -> None:
    path = tmp_path / "install.json"
    m = InstallManifest(
        method="venv-pip",
        package="anygarden-machine",
        python="/p",
        index_url="https://pypi.example.com/simple",
    )
    install_manifest.write(m, path=path)
    assert install_manifest.load(path=path).index_url == "https://pypi.example.com/simple"
