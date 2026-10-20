"""#914 — streaming work-file transfers on a real temporary disk."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from anygarden.machine import managed_workspace as workspace
from anygarden.machine import workspace_transfer as transfer

pytestmark = pytest.mark.skipif(not workspace.supported(), reason="POSIX descriptor boundary")


@pytest.fixture
def managed(tmp_path):
    roots = tmp_path / "agents"
    agent = roots / "agent-a"
    agent.mkdir(parents=True)
    (agent / workspace.RECEIPT_NAME).write_text(json.dumps({
        "schema": 1, "workspace": ".", "pid": 123, "started_at": 100, "generation": 2,
        "engine": "pi-cli", "permission_level": "standard",
        "reported_at": "2026-10-19T12:00:00+00:00",
    }))
    return roots, agent


def _token(roots):
    return workspace.browse(roots, "agent-a", 2).edit_token


def _open(roots, **kwargs):
    return transfer.open_workspace(roots, "agent-a", 2, **kwargs)


@pytest.mark.req("WS-07")
def test_send_streams_a_large_file(managed):
    roots, agent = managed
    payload = bytes(range(256)) * 8192  # 2 MiB
    (agent / "data.bin").write_bytes(payload)
    with _open(roots) as ws, transfer.open_for_send(ws, "data.bin", max_bytes=4 * 1024 * 1024) as (stream, size):
        assert size == len(payload)
        assert b"".join(iter(lambda: stream.read(65536), b"")) == payload
    with pytest.raises(transfer.TransferError) as caught, _open(roots) as ws, transfer.open_for_send(ws, "data.bin", max_bytes=1024):
        pass
    assert caught.value.status == "too_large"


def test_send_refuses_links_private_and_instruction_paths(managed, tmp_path):
    roots, agent = managed
    (tmp_path / "secret").write_text("s")
    (agent / "link").symlink_to(tmp_path / "secret")
    (agent / "AGENTS.md").write_text("rules")
    for path in ("link", ".env", "AGENTS.md", "../agent-a/x"):
        with pytest.raises(transfer.TransferError), _open(roots) as ws, transfer.open_for_send(ws, path, max_bytes=10):
            pass


@pytest.mark.req("WS-07")
def test_receive_creates_the_file_after_checking_size_and_digest(managed):
    roots, agent = managed
    payload = b"x" * (3 * 1024 * 1024)
    digest = hashlib.sha256(payload).hexdigest()
    chunks = [payload[i:i + 65536] for i in range(0, len(payload), 65536)]
    with _open(roots, edit_token=_token(roots)) as ws:
        transfer.receive_into(ws, "upload.bin", chunks, size=len(payload), sha256=digest)
    assert (agent / "upload.bin").read_bytes() == payload
    with pytest.raises(transfer.TransferError) as caught, _open(roots, edit_token=_token(roots)) as ws:
        transfer.receive_into(ws, "upload.bin", [b"y"], size=1, sha256=hashlib.sha256(b"y").hexdigest())
    assert caught.value.status == "conflict"


def test_receive_rejects_wrong_size_or_digest_and_leaves_nothing(managed):
    roots, agent = managed
    for chunks, size, digest in (
        ([b"abc", b"def"], 3, hashlib.sha256(b"abcdef").hexdigest()),
        ([b"abc"], 3, "0" * 64),
        ([b"ab"], 3, hashlib.sha256(b"ab").hexdigest()),
    ):
        with pytest.raises(transfer.TransferError), _open(roots, edit_token=_token(roots)) as ws:
            transfer.receive_into(ws, "bad.bin", chunks, size=size, sha256=digest)
    assert sorted(path.name for path in agent.iterdir()) == [workspace.RECEIPT_NAME]


def test_stale_edit_token_is_refused(managed):
    roots, _agent = managed
    with pytest.raises(transfer.TransferError) as caught, _open(roots, edit_token="f" * 64):
        pass
    assert caught.value.status == "stale"


@pytest.mark.req("WS-07")
def test_archive_skips_private_links_and_instructions(managed, tmp_path):
    roots, agent = managed
    (agent / "AGENTS.md").write_text("rules")
    (agent / "skills").mkdir()
    (agent / ".codex").mkdir()
    (agent / ".codex" / "session.json").write_text("s")
    (agent / "out" / "deep").mkdir(parents=True)
    (agent / "out" / "a.md").write_text("a")
    (agent / "out" / "deep" / "b.bin").write_bytes(b"\x00" * 10)
    (agent / "out" / ".env").write_text("SECRET")
    (tmp_path / "outside").write_text("o")
    (agent / "out" / "link").symlink_to(tmp_path / "outside")
    (agent / "memory").mkdir()
    (agent / "memory" / "notes.md").write_text("n")

    buffer = io.BytesIO()
    with _open(roots) as ws:
        summary = transfer.build_archive(ws, "", buffer, max_entries=100, max_bytes=10_000)
    names = sorted(zipfile.ZipFile(buffer).namelist())
    assert names == ["memory/notes.md", "out/a.md", "out/deep/b.bin"]
    assert summary == transfer.ArchiveSummary(files=3, bytes=12)

    folder = io.BytesIO()
    with _open(roots) as ws:
        transfer.build_archive(ws, "out", folder, max_entries=100, max_bytes=10_000)
    assert sorted(zipfile.ZipFile(folder).namelist()) == ["a.md", "deep/b.bin"]

    without_memory = io.BytesIO()
    with _open(roots) as ws:
        transfer.build_archive(ws, "", without_memory, max_entries=100, max_bytes=10_000, exclude_memory=True)
    assert "memory/notes.md" not in zipfile.ZipFile(without_memory).namelist()


def test_archive_limits(managed):
    roots, agent = managed
    for index in range(5):
        (agent / f"{index}.txt").write_text("x" * 10)
    for kwargs in ({"max_entries": 3, "max_bytes": 10_000}, {"max_entries": 100, "max_bytes": 25}):
        with pytest.raises(transfer.TransferError) as caught, _open(roots) as ws:
            transfer.build_archive(ws, "", io.BytesIO(), **kwargs)
        assert caught.value.status == "too_large"


@pytest.mark.req("AGT-12")
def test_measure_matches_the_move_archive(managed):
    roots, agent = managed
    (agent / "AGENTS.md").write_text("rules")
    (agent / "memory").mkdir()
    (agent / "memory" / "notes.md").write_text("n")
    (agent / "out").mkdir()
    (agent / "out" / "a.md").write_text("aaa")
    (agent / "result.csv").write_text("1,2")
    with _open(roots) as ws:
        summary = transfer.measure(ws, exclude_memory=True)
    buffer = io.BytesIO()
    with _open(roots) as ws:
        packed = transfer.build_archive(ws, "", buffer, max_entries=10, max_bytes=100, exclude_memory=True)
    assert summary == packed == transfer.ArchiveSummary(files=2, bytes=6)
    assert sorted(zipfile.ZipFile(buffer).namelist()) == ["out/a.md", "result.csv"]
