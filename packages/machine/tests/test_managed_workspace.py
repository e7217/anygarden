"""Real temporary-disk coverage of the read-only managed workspace boundary."""

from __future__ import annotations

import json
import os

import pytest
from anygarden_machine import managed_workspace as workspace

pytestmark = pytest.mark.skipif(
    not workspace.supported(), reason="POSIX descriptor boundary"
)


@pytest.fixture
def managed(tmp_path):
    roots = tmp_path / "agents"
    agent = roots / "agent-a"
    agent.mkdir(parents=True)
    (agent / workspace.RECEIPT_NAME).write_text(
        json.dumps(
            {
                "schema": 1,
                "workspace": ".",
                "pid": 123,
                "started_at": 100,
                "generation": 2,
                "engine": "pi-cli",
                "permission_level": "standard",
                "reported_at": "2026-09-26T12:00:00+00:00",
            }
        )
    )
    return roots, agent


def test_actual_files_and_bounded_pages(managed):
    roots, agent = managed
    for index in range(205):
        (agent / f"file-{index:03}.txt").write_text(str(index))
    first = workspace.browse(roots, "agent-a", 2)
    assert first.status == "ready"
    assert first.cwd == str(agent)
    assert len(first.entries) == 200
    second = workspace.browse(roots, "agent-a", 2, cursor=first.next_cursor)
    assert len(second.entries) == 5
    assert second.next_cursor is None
    assert len({entry.name for entry in first.entries + second.entries}) == 205
    (agent / "folder").mkdir()
    (agent / "folder" / "note.md").write_text("persisted work", encoding="utf-8")
    read = workspace.browse(
        roots, "agent-a", 2, operation="read", path="folder/note.md"
    )
    assert read.text == "persisted work"
    assert read.preview_status == "text"
    # No live process is needed to read files retained after a stop/restart.
    assert read.live is False
    assert (
        workspace.browse(
            roots, "agent-a", 3, operation="read", path="folder/note.md"
        ).text
        == read.text
    )


def test_reported_child_and_process_generation_fences(managed):
    roots, agent = managed
    (agent / "workspace").mkdir()
    (agent / "workspace" / "report.txt").write_text("child")
    receipt = json.loads((agent / workspace.RECEIPT_NAME).read_text())
    receipt["workspace"] = "workspace"
    (agent / workspace.RECEIPT_NAME).write_text(json.dumps(receipt))
    ready = workspace.browse(
        roots,
        "agent-a",
        2,
        running_generation=2,
        running_pid=123,
        running_started_at=100,
    )
    assert ready.cwd == str(agent / "workspace")
    assert ready.live is True
    for overrides in (
        {"running_pid": 999},
        {"running_generation": 1},
        {"running_started_at": 200},
    ):
        values = {
            "running_generation": 2,
            "running_pid": 123,
            "running_started_at": 100,
        } | overrides
        assert workspace.browse(roots, "agent-a", 2, **values).status == "stale"
    assert workspace.browse(roots, "agent-a", 1).status == "stale"


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "a/../b",
        "a//b",
        "a\\b",
        "a\x00b",
        ".codex/auth.json",
        ".pi/auth.json",
        ".env",
        "nested/.env.local",
        "nested/auth.json",
        "secret.key",
        "manifest.json",
        "runtime.json",
    ],
)
def test_private_and_invalid_paths_fail_closed(managed, path):
    roots, _ = managed
    assert (
        workspace.browse(roots, "agent-a", 2, operation="read", path=path).status
        == "blocked"
    )


def test_private_files_are_excluded_from_listing(managed):
    roots, agent = managed
    for name in (
        ".env",
        ".env.production",
        "auth.json",
        "credentials.json",
        "secret.pem",
        "manifest.json",
        "runtime.json",
        ".gitignore",
        "result.md",
    ):
        (agent / name).write_text("private")
    (agent / ".pi").mkdir()
    assert [entry.name for entry in workspace.browse(roots, "agent-a", 2).entries] == [
        ".gitignore",
        "result.md",
    ]


def test_links_and_special_files_never_open(managed, tmp_path):
    roots, agent = managed
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("private")
    (agent / "parent").symlink_to(outside, target_is_directory=True)
    (agent / "leaf.txt").symlink_to(secret)
    os.link(secret, agent / "alias.txt")
    os.mkfifo(agent / "pipe")
    for path in ("parent/secret.txt", "leaf.txt", "alias.txt", "pipe"):
        assert (
            workspace.browse(roots, "agent-a", 2, operation="read", path=path).status
            == "blocked"
        )
    entries = {
        entry.name: entry.kind
        for entry in workspace.browse(roots, "agent-a", 2).entries
    }
    assert entries == {
        "alias.txt": "unavailable",
        "leaf.txt": "link",
        "parent": "link",
        "pipe": "unavailable",
    }
    (roots / "linked-agent").symlink_to(agent, target_is_directory=True)
    assert workspace.browse(roots, "linked-agent", 2).status == "blocked"


def test_directory_swap_cannot_redirect_an_already_open_descriptor(
    managed, tmp_path, monkeypatch
):
    roots, agent = managed
    folder = agent / "folder"
    folder.mkdir()
    (folder / "note.txt").write_text("safe")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_text("private")
    original_open = os.open

    def swapped(path, flags, *args, **kwargs):
        if path == "note.txt":
            folder.rename(agent / "previous")
            folder.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped)
    assert (
        workspace.browse(
            roots, "agent-a", 2, operation="read", path="folder/note.txt"
        ).text
        == "safe"
    )


def test_binary_large_files_and_scan_bounds(managed, monkeypatch):
    roots, agent = managed
    (agent / "binary.bin").write_bytes(b"\x00\xff")
    (agent / "large.txt").write_bytes(b"x" * (workspace.PREVIEW_BYTES + 1))
    assert (
        workspace.browse(
            roots, "agent-a", 2, operation="read", path="binary.bin"
        ).preview_status
        == "binary"
    )
    large = workspace.browse(roots, "agent-a", 2, operation="read", path="large.txt")
    assert large.preview_status == "too_large"
    assert large.text is None
    monkeypatch.setattr(workspace, "SCAN_LIMIT", 1)
    assert workspace.browse(roots, "agent-a", 2).status == "too_many_entries"


def test_missing_receipt_and_unsupported_platform_are_explicit(managed, monkeypatch):
    roots, agent = managed
    (agent / workspace.RECEIPT_NAME).unlink()
    assert workspace.browse(roots, "agent-a", 2).status == "not_ready"
    monkeypatch.setattr(workspace, "supported", lambda: False)
    assert workspace.browse(roots, "agent-a", 2).status == "unsupported"


def test_respawn_materialization_preserves_receipt_and_agent_output(
    managed, tmp_path, monkeypatch
):
    from anygarden_machine.spawner import Spawner, SpawnManifest

    roots, agent = managed
    # Keep the materializer's host-auth fallback inside this isolated fixture.
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    manifest = SpawnManifest(
        agent_id="agent-a", engine="codex-cli", agent_token="fixture"
    )
    spawner = Spawner(agent_dirs_root=roots)
    spawner._materialize_agent_dir(manifest)
    receipt = json.loads((agent / workspace.RECEIPT_NAME).read_text())
    receipt["workspace"] = "workspace"
    (agent / workspace.RECEIPT_NAME).write_text(json.dumps(receipt))
    (agent / "workspace" / "persistent.txt").write_text("survives")
    # A new spawner represents a restarted daemon; reconciliation must keep
    # runtime-created output and its reported location while updating context.
    Spawner(agent_dirs_root=roots)._materialize_agent_dir(manifest)
    result = workspace.browse(
        roots, "agent-a", 3, operation="read", path="persistent.txt"
    )
    assert result.text == "survives"
    assert result.live is False
