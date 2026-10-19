"""Real temporary-disk coverage of the read-only managed workspace boundary."""

from __future__ import annotations

import base64
import json
import os

import pytest
from anygarden.machine import managed_workspace as workspace

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


def test_create_upload_and_edit_use_actual_managed_disk(managed):
    roots, agent = managed
    token = workspace.browse(roots, "agent-a", 2).edit_token
    assert token
    folder = workspace.mutate(
        roots, "agent-a", 2, operation="mkdir", path="notes", edit_token=token
    )
    assert folder.status == "ready" and (agent / "notes").is_dir()
    created = workspace.mutate(
        roots,
        "agent-a",
        2,
        operation="write",
        path="notes/todo.md",
        edit_token=token,
        text="first",
        expected_sha256="absent",
    )
    assert created.status == "ready" and created.sha256
    saved = workspace.mutate(
        roots,
        "agent-a",
        2,
        operation="write",
        path="notes/todo.md",
        edit_token=token,
        text="changed",
        expected_sha256=created.sha256,
    )
    assert saved.text == "changed"
    assert (agent / "notes" / "todo.md").read_text() == "changed"
    payload = base64.b64encode(b"\0binary\xff").decode()
    uploaded = workspace.mutate(
        roots,
        "agent-a",
        2,
        operation="upload",
        path="notes/image.bin",
        edit_token=token,
        content_base64=payload,
    )
    assert uploaded.status == "ready" and uploaded.preview_status == "binary"
    assert (agent / "notes" / "image.bin").read_bytes() == b"\0binary\xff"
    assert (
        workspace.mutate(
            roots,
            "agent-a",
            2,
            operation="upload",
            path="notes/image.bin",
            edit_token=token,
            content_base64=base64.b64encode(b"overwrite").decode(),
        ).status
        == "conflict"
    )
    assert (agent / "notes" / "image.bin").read_bytes() == b"\0binary\xff"


@pytest.mark.req("WS-02")
def test_writes_without_a_valid_edit_token_are_rejected(managed):
    roots, agent = managed
    valid = workspace.browse(roots, "agent-a", 2).edit_token
    assert valid
    forged = ("0" if valid[0] != "0" else "1") + valid[1:]
    for token, expected in ((forged, "stale"), ("", "blocked"), ("ab" * 10, "blocked")):
        for operation, extra in (
            ("mkdir", {}),
            ("write", {"text": "x", "expected_sha256": "absent"}),
            ("upload", {"content_base64": base64.b64encode(b"x").decode()}),
        ):
            result = workspace.mutate(
                roots,
                "agent-a",
                2,
                operation=operation,
                path="forged",
                edit_token=token,
                **extra,
            )
            assert result.status == expected, (token, operation)
            assert not (agent / "forged").exists()


@pytest.mark.req("WS-02")
def test_edits_reject_stale_content_receipt_private_and_unsafe_paths(managed, tmp_path):
    roots, agent = managed
    token = workspace.browse(roots, "agent-a", 2).edit_token
    (agent / "safe.txt").write_text("original")
    expected = workspace.browse(
        roots, "agent-a", 2, operation="read", path="safe.txt"
    ).sha256
    (agent / "safe.txt").write_text("agent updated")
    assert (
        workspace.mutate(
            roots,
            "agent-a",
            2,
            operation="write",
            path="safe.txt",
            edit_token=token,
            text="web updated",
            expected_sha256=expected,
        ).status
        == "conflict"
    )
    assert (agent / "safe.txt").read_text() == "agent updated"
    receipt = json.loads((agent / workspace.RECEIPT_NAME).read_text())
    receipt["reported_at"] = "2026-09-27T12:00:00+00:00"
    (agent / workspace.RECEIPT_NAME).write_text(json.dumps(receipt))
    assert (
        workspace.mutate(
            roots,
            "agent-a",
            2,
            operation="mkdir",
            path="stale",
            edit_token=token,
        ).status
        == "stale"
    )
    assert not (agent / "stale").exists()
    token = workspace.browse(roots, "agent-a", 2).edit_token
    outside = tmp_path / "outside"
    outside.mkdir()
    (agent / "link").symlink_to(outside, target_is_directory=True)
    (agent / "secret.key").write_text("protected")
    os.link(agent / "safe.txt", agent / "alias.txt")
    for path in ("../escape", "link/malicious", "secret.key", "alias.txt", ".env"):
        assert (
            workspace.mutate(
                roots,
                "agent-a",
                2,
                operation="write",
                path=path,
                edit_token=token,
                text="bad",
                expected_sha256="absent",
            ).status
            == "blocked"
        )
    assert not (outside / "malicious").exists()
    assert (agent / "secret.key").read_text() == "protected"


def test_upload_limit_and_bad_encoding_do_not_create_files(managed):
    roots, agent = managed
    token = workspace.browse(roots, "agent-a", 2).edit_token
    assert (
        workspace.mutate(
            roots,
            "agent-a",
            2,
            operation="upload",
            path="large.bin",
            edit_token=token,
            content_base64=base64.b64encode(
                b"x" * (workspace.MAX_UPLOAD_BYTES + 1)
            ).decode(),
        ).status
        == "too_large"
    )
    assert (
        workspace.mutate(
            roots,
            "agent-a",
            2,
            operation="upload",
            path="bad.bin",
            edit_token=token,
            content_base64="!!!",
        ).status
        == "blocked"
    )
    assert not (agent / "large.bin").exists()
    assert not (agent / "bad.bin").exists()


def test_respawn_materialization_preserves_receipt_and_agent_output(
    managed, tmp_path, monkeypatch
):
    from anygarden.machine.spawner import Spawner, SpawnManifest

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


def _token(roots, agent_id="agent-a"):
    return workspace.browse(roots, agent_id, 2).edit_token


@pytest.mark.req("WS-02")
def test_fetch_returns_bytes_up_to_the_download_limit(managed, monkeypatch):
    roots, agent = managed
    (agent / "chart.png").write_bytes(b"\x89PNG\x00data")
    fetched = workspace.browse(roots, "agent-a", 2, operation="fetch", path="chart.png")
    assert base64.b64decode(fetched.content_base64) == b"\x89PNG\x00data"
    monkeypatch.setattr(workspace, "MAX_DOWNLOAD_BYTES", 4)
    big = workspace.browse(roots, "agent-a", 2, operation="fetch", path="chart.png")
    assert big.content_base64 is None and big.preview_status == "too_large"


@pytest.mark.req("WS-02")
def test_delete_and_rename(managed):
    roots, agent = managed
    (agent / "out").mkdir()
    (agent / "out" / "a.md").write_text("a")
    (agent / "out" / "deep").mkdir()
    (agent / "out" / "deep" / "b.md").write_text("b")
    (agent / "keep.md").write_text("k")

    renamed = workspace.mutate(roots, "agent-a", 2, operation="rename", path="keep.md",
                               new_name="kept.md", edit_token=_token(roots))
    assert renamed.status == "ready"
    assert (agent / "kept.md").read_text() == "k"
    clash = workspace.mutate(roots, "agent-a", 2, operation="rename", path="kept.md",
                             new_name="out", edit_token=_token(roots))
    assert clash.status == "conflict"

    deleted = workspace.mutate(roots, "agent-a", 2, operation="delete", path="out",
                               edit_token=_token(roots))
    assert deleted.status == "ready"
    assert not (agent / "out").exists()
    missing = workspace.mutate(roots, "agent-a", 2, operation="delete", path="out",
                               edit_token=_token(roots))
    assert missing.status == "not_found"


def test_delete_refuses_huge_trees_and_links(managed, monkeypatch, tmp_path):
    roots, agent = managed
    (agent / "many").mkdir()
    for index in range(5):
        (agent / "many" / f"{index}.txt").write_text("x")
    monkeypatch.setattr(workspace, "MAX_DELETE_ENTRIES", 3)
    refused = workspace.mutate(roots, "agent-a", 2, operation="delete", path="many",
                               edit_token=_token(roots))
    assert refused.status == "blocked"
    assert len(list((agent / "many").iterdir())) == 5
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("p")
    (agent / "link").symlink_to(outside)
    linked = workspace.mutate(roots, "agent-a", 2, operation="delete", path="link",
                              edit_token=_token(roots))
    assert linked.status == "blocked"
    assert (outside / "precious.txt").exists()
    bad_name = workspace.mutate(roots, "agent-a", 2, operation="rename", path="many",
                                new_name="../escape", edit_token=_token(roots))
    assert bad_name.status == "blocked"


@pytest.mark.req("WS-02")
def test_instruction_files_are_hidden_from_the_work_files(managed):
    roots, agent = managed
    (agent / "AGENTS.md").write_text("rules")
    (agent / "skills").mkdir()
    (agent / "result.md").write_text("r")
    listing = workspace.browse(roots, "agent-a", 2)
    assert [entry.name for entry in listing.entries] == ["result.md"]
    for path in ("AGENTS.md", "skills/x/SKILL.md"):
        assert workspace.browse(roots, "agent-a", 2, operation="read", path=path).status == "blocked"
    write = workspace.mutate(roots, "agent-a", 2, operation="write", path="AGENTS.md",
                             text="x", expected_sha256="absent", edit_token=_token(roots))
    assert write.status == "blocked"
    assert (agent / "AGENTS.md").read_text() == "rules"
