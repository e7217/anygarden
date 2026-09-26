"""The real room adapter reports the cwd actually passed to the engine."""

from __future__ import annotations

import json

import pytest
from anygarden_agent.runtime.workspace_receipt import RECEIPT_NAME, report_workspace

from .test_room_execution import client_for, message
from .test_room_execution import setup_room as _setup_room

workspace_room = _setup_room


@pytest.mark.parametrize("engine", ["codex-cli", "pi-cli"])
async def test_runtime_reports_selected_workspace_and_preserves_files(
    workspace_room, monkeypatch, engine
):
    root = workspace_room
    client = await client_for(engine, monkeypatch)
    try:
        receipt = json.loads((root / RECEIPT_NAME).read_text())
        assert receipt["workspace"] == "workspace"
        assert receipt["engine"] == engine
        assert receipt["generation"] == 7
        assert receipt["permission_level"] == "trusted"
        await client._message_handlers[0](message("workspace-turn"))
        assert (root / "workspace" / "measured").exists()
        assert json.loads((root / RECEIPT_NAME).read_text())["workspace"] == "workspace"
    finally:
        await client.close()
    client = await client_for(engine, monkeypatch)
    try:
        assert (root / "workspace" / "measured").exists()
        assert json.loads((root / RECEIPT_NAME).read_text())["generation"] == 7
    finally:
        await client.close()


async def test_runtime_reports_root_without_child_workspace(
    workspace_room, monkeypatch
):
    root = workspace_room
    (root / "workspace").rmdir()
    client = await client_for("codex-cli", monkeypatch)
    try:
        assert json.loads((root / RECEIPT_NAME).read_text())["workspace"] == "."
        await client._message_handlers[0](message("root-turn"))
        assert (root / "measured").exists()
    finally:
        await client.close()


def test_receipt_replaces_symlink_without_writing_to_its_target(tmp_path):
    root = tmp_path / "agent"
    root.mkdir()
    secret = tmp_path / "outside"
    secret.write_text("unchanged")
    (root / RECEIPT_NAME).symlink_to(secret)
    report_workspace(
        root, root, generation=1, engine="codex-cli", permission_level="standard"
    )
    assert secret.read_text() == "unchanged"
    assert not (root / RECEIPT_NAME).is_symlink()
    assert (root / RECEIPT_NAME).stat().st_mode & 0o777 == 0o600
    content = (root / RECEIPT_NAME).read_text()
    report_workspace(
        root, tmp_path, generation=1, engine="codex-cli", permission_level="standard"
    )
    assert (root / RECEIPT_NAME).read_text() == content
