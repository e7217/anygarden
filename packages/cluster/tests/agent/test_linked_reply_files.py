"""#1017 — files a reply links by a local path are attached to the reply.

The supervisor runs outside the engine sandbox, so only the agent's own
output areas (``workspace/`` and ``memory/`` minus the inbox and shared
folders) qualify, and never through a hidden path component.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from anygarden.agent.runtime.attachments import (
    extract_local_links,
    resolve_linked_file,
)
from anygarden.agent.runtime.handler_wrapper import RoomHandlerSupervisor

ROOM = "11111111-2222-3333-4444-555555555555"


@dataclass
class _FakeClient:
    sends: list[tuple[str, str, dict | None]] = field(default_factory=list)
    uploads: list[tuple[str, str, str]] = field(default_factory=list)

    async def sendLifecycle(self, room_id, request_id, event, **details):
        pass

    async def send(self, room_id, content, metadata=None, thread_root_id=None):
        self.sends.append((room_id, content, metadata))

    async def upload_room_artifact(self, room_id, path, mime):
        self.uploads.append((room_id, Path(path).name, mime))
        return {"id": f"art-{Path(path).name}"}


def _agent_root(tmp_path: Path) -> Path:
    root = tmp_path / "agent"
    (root / "workspace").mkdir(parents=True)
    (root / "memory" / "outbox" / ROOM).mkdir(parents=True)
    (root / "memory" / "inbox" / ROOM).mkdir(parents=True)
    (root / "memory" / "shared").mkdir(parents=True)
    # The codex workspace bridge: ``workspace/memory`` -> ``memory``.
    os.symlink(root / "memory", root / "workspace" / "memory")
    return root


@pytest.mark.req("MSG-11")
def test_extract_links_skips_code_and_remote_targets() -> None:
    text = (
        "See [report](workspace/report.md) and ![chart](./chart.png).\n"
        "Also <file:///tmp/x.csv> and [site](https://example.com/a.md).\n"
        "`[inline](inline.md)`\n"
        "```\n[fenced](fenced.md)\n```\n"
        "[titled](out/data.json \"Data\") [anchor](#top) [again](workspace/report.md)\n"
        "[spaced](<my file.txt>) [encoded](a%20b.txt)\n"
    )
    assert extract_local_links(text) == [
        "workspace/report.md",
        "./chart.png",
        "file:///tmp/x.csv",
        "out/data.json",
        "my file.txt",
        "a%20b.txt",
    ]


@pytest.mark.req("MSG-11")
def test_resolve_relative_paths_against_workspace_then_root(tmp_path: Path) -> None:
    root = _agent_root(tmp_path)
    (root / "workspace" / "report.md").write_text("r")
    (root / "workspace" / "a b.txt").write_text("x")
    out = root / "memory" / "outbox" / ROOM / "chart.png"
    out.write_bytes(b"png")

    assert resolve_linked_file("report.md", root) == (root / "workspace" / "report.md").resolve()
    assert resolve_linked_file("workspace/report.md", root) == (root / "workspace" / "report.md").resolve()
    assert resolve_linked_file("a%20b.txt", root) == (root / "workspace" / "a b.txt").resolve()
    assert resolve_linked_file(f"memory/outbox/{ROOM}/chart.png", root) == out.resolve()
    assert resolve_linked_file(str(root / "workspace" / "report.md"), root) is not None
    assert resolve_linked_file(f"file://{root}/workspace/report.md?x=1#y", root) is not None


@pytest.mark.req("MSG-11")
def test_resolve_rejects_paths_outside_the_output_areas(tmp_path: Path) -> None:
    root = _agent_root(tmp_path)
    (root / "agent.log").write_text("log")
    (root / "manifest.json").write_text("{}")
    (root / ".pi" / "agent").mkdir(parents=True)
    (root / ".pi" / "agent" / "auth.json").write_text("secret")
    (root / "workspace" / ".env").write_text("secret")
    (root / "workspace" / ".git").mkdir()
    (root / "workspace" / ".git" / "config").write_text("x")
    (root / "memory" / "inbox" / ROOM / "upload.png").write_bytes(b"x")
    (root / "memory" / "shared" / "brief.md").write_text("x")
    (root / "workspace" / "dir").mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("x")
    os.symlink(outside, root / "workspace" / "escape.txt")
    os.symlink(root / "agent.log", root / "workspace" / "log.txt")

    for href in (
        "agent.log",
        str(root / "manifest.json"),
        ".pi/agent/auth.json",
        "../.pi/agent/auth.json",
        ".env",
        ".git/config",
        f"memory/inbox/{ROOM}/upload.png",
        "memory/shared/brief.md",
        "../secret.txt",
        str(outside),
        "escape.txt",
        "log.txt",
        "dir",
        "missing.md",
        "",
    ):
        assert resolve_linked_file(href, root) is None, href


@pytest.mark.req("MSG-11")
@pytest.mark.asyncio
async def test_supervisor_attaches_linked_files_and_maps_their_links(tmp_path: Path) -> None:
    root = _agent_root(tmp_path)
    (root / "workspace" / "data.csv").write_text("a,b")
    (root / "workspace" / "app.zip").write_bytes(b"PK")
    client = _FakeClient()
    sup = RoomHandlerSupervisor(
        client=client, engine_name="codex",
        outbox_dir=root / "memory" / "outbox", agent_root=root,
    )

    async def run_engine():
        (root / "memory" / "outbox" / ROOM / "chart.png").write_bytes(b"png")
        return (
            f"[chart](memory/outbox/{ROOM}/chart.png) [data](data.csv) "
            "[zip](workspace/app.zip) [same](./data.csv) [nope](agent.log)"
        )

    await sup.dispatch(room_id=ROOM, request_id="req-1", run_engine=run_engine)

    assert client.uploads == [
        (ROOM, "chart.png", "image/png"),
        (ROOM, "data.csv", "text/csv"),
        (ROOM, "app.zip", "application/zip"),
    ]
    metadata = client.sends[0][2]
    assert metadata["references"] == [
        {"type": "artifact", "id": "art-chart.png"},
        {"type": "artifact", "id": "art-data.csv"},
        {"type": "artifact", "id": "art-app.zip"},
    ]
    assert metadata["linked_files"] == [
        {"href": f"memory/outbox/{ROOM}/chart.png", "artifact_id": "art-chart.png"},
        {"href": "data.csv", "artifact_id": "art-data.csv"},
        {"href": "workspace/app.zip", "artifact_id": "art-app.zip"},
        {"href": "./data.csv", "artifact_id": "art-data.csv"},
    ]
    assert "undelivered_attachments" not in metadata


@pytest.mark.req("MSG-11")
@pytest.mark.asyncio
async def test_linked_files_share_the_reply_attachment_cap(tmp_path: Path) -> None:
    root = _agent_root(tmp_path)
    links = []
    for i in range(12):
        (root / "workspace" / f"f{i}.txt").write_text(str(i))
        links.append(f"[f{i}](f{i}.txt)")
    client = _FakeClient()
    sup = RoomHandlerSupervisor(
        client=client, engine_name="codex",
        outbox_dir=root / "memory" / "outbox", agent_root=root,
    )

    async def run_engine():
        return " ".join(links)

    await sup.dispatch(room_id=ROOM, request_id="req-1", run_engine=run_engine)

    metadata = client.sends[0][2]
    assert len(metadata["references"]) == 10
    assert metadata["undelivered_attachments"] == [
        {"name": "f10.txt", "reason": "too_many"},
        {"name": "f11.txt", "reason": "too_many"},
    ]


@pytest.mark.req("MSG-11")
@pytest.mark.asyncio
async def test_oversized_linked_file_is_reported_without_reading_it(tmp_path: Path) -> None:
    root = _agent_root(tmp_path)
    big = root / "workspace" / "big.bin"
    with big.open("wb") as fh:
        fh.truncate(21 * 1024 * 1024)
    client = _FakeClient()
    sup = RoomHandlerSupervisor(
        client=client, engine_name="codex",
        outbox_dir=root / "memory" / "outbox", agent_root=root,
    )

    async def run_engine():
        return "[big](big.bin)"

    await sup.dispatch(room_id=ROOM, request_id="req-1", run_engine=run_engine)

    assert client.uploads == []
    assert client.sends[0][2]["undelivered_attachments"] == [
        {"name": "big.bin", "reason": "too_large"},
    ]
