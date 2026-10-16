"""#854 — files an agent writes to ``memory/outbox/<room_id>/`` during a
turn are uploaded and attached to that turn's reply."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from anygarden.agent.client import ArtifactUploadError
from anygarden.agent.runtime.attachments import (
    changed_outbox_files,
    snapshot_outbox,
    upload_reply_attachments,
)
from anygarden.agent.runtime.handler_wrapper import RoomHandlerSupervisor

ROOM = "11111111-2222-3333-4444-555555555555"


@dataclass
class _FakeClient:
    sends: list[tuple[str, str, dict | None]] = field(default_factory=list)
    uploads: list[tuple[str, str, str]] = field(default_factory=list)
    fail: dict[str, int] = field(default_factory=dict)

    async def sendLifecycle(self, room_id, request_id, event, **details):
        pass

    async def send(self, room_id, content, metadata=None, thread_root_id=None):
        self.sends.append((room_id, content, metadata))

    async def upload_room_artifact(self, room_id, path, mime):
        self.uploads.append((room_id, Path(path).name, mime))
        status = self.fail.get(Path(path).name)
        if status is not None:
            raise ArtifactUploadError(status)
        return {"id": f"art-{Path(path).name}"}


def _room_dir(tmp_path: Path) -> Path:
    room_dir = tmp_path / "outbox" / ROOM
    room_dir.mkdir(parents=True)
    return room_dir


def test_snapshot_missing_dir_is_empty(tmp_path: Path) -> None:
    assert snapshot_outbox(tmp_path / "outbox", ROOM) == {}
    assert snapshot_outbox(None, ROOM) == {}


def test_snapshot_rejects_non_uuid_room(tmp_path: Path) -> None:
    (tmp_path / "outbox" / "..").mkdir(parents=True, exist_ok=True)
    assert snapshot_outbox(tmp_path / "outbox", "../etc") == {}


def test_changed_files_include_new_and_modified_only(tmp_path: Path) -> None:
    room_dir = _room_dir(tmp_path)
    (room_dir / "old.png").write_bytes(b"old")
    (room_dir / "edited.txt").write_bytes(b"v1")
    before = snapshot_outbox(tmp_path / "outbox", ROOM)

    (room_dir / "edited.txt").write_bytes(b"v2")
    (room_dir / "new.png").write_bytes(b"new")
    (room_dir / "nested").mkdir()
    (room_dir / "nested" / "deep.png").write_bytes(b"x")
    os.symlink(room_dir / "new.png", room_dir / "link.png")

    changed = changed_outbox_files(tmp_path / "outbox", ROOM, before)
    assert [p.name for p in changed] == ["edited.txt", "new.png"]


@pytest.mark.asyncio
async def test_upload_builds_references_and_undelivered(tmp_path: Path) -> None:
    room_dir = _room_dir(tmp_path)
    for name in ("a.png", "big.png", "weird.bin", "boom.txt"):
        (room_dir / name).write_bytes(name.encode())
    client = _FakeClient(fail={"big.png": 413, "weird.bin": 415, "boom.txt": 500})

    refs, undelivered = await upload_reply_attachments(
        client, ROOM, sorted(room_dir.iterdir())
    )

    assert refs == [{"type": "artifact", "id": "art-a.png"}]
    assert undelivered == [
        {"name": "big.png", "reason": "too_large"},
        {"name": "boom.txt", "reason": "upload_failed"},
        {"name": "weird.bin", "reason": "unsupported_type"},
    ]
    assert client.uploads[0] == (ROOM, "a.png", "image/png")


@pytest.mark.asyncio
async def test_upload_caps_attachment_count(tmp_path: Path) -> None:
    room_dir = _room_dir(tmp_path)
    paths = []
    for i in range(3):
        path = room_dir / f"{i}.png"
        path.write_bytes(bytes([i]))
        paths.append(path)
    client = _FakeClient()

    refs, undelivered = await upload_reply_attachments(client, ROOM, paths, limit=2)

    assert [r["id"] for r in refs] == ["art-0.png", "art-1.png"]
    assert undelivered == [{"name": "2.png", "reason": "too_many"}]
    assert len(client.uploads) == 2


@pytest.mark.asyncio
async def test_supervisor_attaches_files_written_during_turn(tmp_path: Path) -> None:
    room_dir = _room_dir(tmp_path)
    (room_dir / "before.png").write_bytes(b"already here")
    client = _FakeClient()
    sup = RoomHandlerSupervisor(
        client=client,
        engine_name="codex",
        engine_timeout=5.0,
        outbox_dir=tmp_path / "outbox",
    )

    async def run_engine():
        (room_dir / "chart.png").write_bytes(b"\x89PNG chart")
        return "here is the chart"

    await sup.dispatch(room_id=ROOM, request_id="req-1", run_engine=run_engine)

    assert client.uploads == [(ROOM, "chart.png", "image/png")]
    assert client.sends == [(
        ROOM,
        "here is the chart",
        {
            "request_id": "req-1",
            "turn_outcome": "ok",
            "references": [{"type": "artifact", "id": "art-chart.png"}],
        },
    )]


@pytest.mark.asyncio
async def test_supervisor_reports_failed_upload_and_still_replies(
    tmp_path: Path,
) -> None:
    room_dir = _room_dir(tmp_path)
    client = _FakeClient(fail={"huge.png": 413})
    sup = RoomHandlerSupervisor(
        client=client,
        engine_name="codex",
        engine_timeout=5.0,
        outbox_dir=tmp_path / "outbox",
    )

    async def run_engine():
        (room_dir / "huge.png").write_bytes(b"big")
        return "done"

    await sup.dispatch(room_id=ROOM, request_id="req-2", run_engine=run_engine)

    assert client.sends == [(
        ROOM,
        "done",
        {
            "request_id": "req-2",
            "turn_outcome": "ok",
            "undelivered_attachments": [{"name": "huge.png", "reason": "too_large"}],
        },
    )]


@pytest.mark.asyncio
async def test_supervisor_without_outbox_dir_sends_plain_reply() -> None:
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return "hi"

    await sup.dispatch(room_id=ROOM, request_id="req-3", run_engine=run_engine)

    assert client.uploads == []
    assert client.sends == [(ROOM, "hi", {"request_id": "req-3", "turn_outcome": "ok"})]
