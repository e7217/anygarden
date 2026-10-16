"""#874 — files a message attaches reach the agent as local paths.

Before the engine runs, the runtime downloads the turn's ``artifact``
references into ``memory/inbox/<room_id>/`` with the agent's own token, so
an agent on a remote machine gets the same file as a local one. The turn
prompt then lists each path; the engine opens the file with its own tools.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from anygarden.agent.client import ArtifactDownloadError, ChatClient
from anygarden.agent.integrations.base import compose_inbox_attachments_hint
from anygarden.agent.integrations import codex_cli
from anygarden.agent.integrations.codex_cli import CodexCliAdapter
from anygarden.agent.runtime.attachments import (
    InboxAttachment,
    materialize_inbox_attachments,
)

ROOM = "11111111-2222-3333-4444-555555555555"
OTHER_ROOM = "99999999-2222-3333-4444-555555555555"
PNG = b"\x89PNG\r\n\x1a\n pixels"


def _ref(artifact_id: str, name: str, body: bytes = PNG, *, room_id: str = ROOM, mime: str = "image/png") -> dict:
    return {
        "type": "artifact",
        "id": artifact_id,
        "room_id": room_id,
        "name": name,
        "mime": mime,
        "size_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


@dataclass
class _FakeClient:
    blobs: dict[str, bytes] = field(default_factory=dict)
    fail: dict[str, int | None] = field(default_factory=dict)
    downloads: list[str] = field(default_factory=list)

    async def download_room_artifact(self, room_id, artifact_id, dest, *, max_bytes):
        self.downloads.append(artifact_id)
        if artifact_id in self.fail:
            raise ArtifactDownloadError(self.fail[artifact_id])
        body = self.blobs[artifact_id]
        if len(body) > max_bytes:
            raise ArtifactDownloadError(413)
        Path(dest).write_bytes(body)
        return len(body)


def _meta(*refs: dict) -> dict:
    return {"references": list(refs)}


class TestMaterialize:
    async def test_downloads_into_the_room_inbox(self, tmp_path: Path) -> None:
        client = _FakeClient(blobs={"a1b2c3d4-0000": PNG})
        entries = await materialize_inbox_attachments(
            client, tmp_path, ROOM, _meta(_ref("a1b2c3d4-0000", "shot.png"))
        )
        assert entries == [
            InboxAttachment(
                name="shot.png",
                mime="image/png",
                size_bytes=len(PNG),
                path=f"memory/inbox/{ROOM}/a1b2c3d4-shot.png",
            )
        ]
        assert (tmp_path / ROOM / "a1b2c3d4-shot.png").read_bytes() == PNG

    async def test_same_bytes_are_not_downloaded_again(self, tmp_path: Path) -> None:
        client = _FakeClient(blobs={"a1b2c3d4-0000": PNG})
        meta = _meta(_ref("a1b2c3d4-0000", "shot.png"))
        await materialize_inbox_attachments(client, tmp_path, ROOM, meta)
        again = await materialize_inbox_attachments(client, tmp_path, ROOM, meta)
        assert client.downloads == ["a1b2c3d4-0000"]
        assert again[0].path is not None

    async def test_changed_bytes_are_downloaded_again(self, tmp_path: Path) -> None:
        client = _FakeClient(blobs={"a1b2c3d4-0000": PNG})
        target = tmp_path / ROOM / "a1b2c3d4-shot.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"stale")
        await materialize_inbox_attachments(
            client, tmp_path, ROOM, _meta(_ref("a1b2c3d4-0000", "shot.png"))
        )
        assert target.read_bytes() == PNG

    async def test_only_artifacts_of_this_room(self, tmp_path: Path) -> None:
        client = _FakeClient(blobs={"x": PNG, "y": PNG})
        entries = await materialize_inbox_attachments(
            client,
            tmp_path,
            ROOM,
            _meta(
                {"type": "shared_file", "id": "f", "name": "a.md", "storage_name": "a.md"},
                _ref("y", "elsewhere.png", room_id=OTHER_ROOM),
                _ref("x", "here.png"),
            ),
        )
        assert [e.name for e in entries] == ["here.png"]
        assert client.downloads == ["x"]

    @pytest.mark.parametrize(
        ("name", "stored"),
        [("../../etc/passwd.png", "passwd.png"), ("a/b\\c.png", "c.png"), ("..", "attachment")],
    )
    async def test_names_cannot_escape_the_inbox(self, tmp_path: Path, name: str, stored: str) -> None:
        client = _FakeClient(blobs={"abcdefgh-1": PNG})
        entries = await materialize_inbox_attachments(
            client, tmp_path, ROOM, _meta(_ref("abcdefgh-1", name))
        )
        assert entries[0].path == f"memory/inbox/{ROOM}/abcdefgh-{stored}"
        assert [p.name for p in (tmp_path / ROOM).iterdir()] == [f"abcdefgh-{stored}"]

    async def test_room_id_must_be_a_uuid(self, tmp_path: Path) -> None:
        client = _FakeClient(blobs={"x": PNG})
        entries = await materialize_inbox_attachments(
            client, tmp_path, "../escape", _meta(_ref("x", "a.png", room_id="../escape"))
        )
        assert entries == []
        assert client.downloads == []

    async def test_a_symlinked_room_dir_is_refused(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        os.symlink(outside, inbox / ROOM)
        client = _FakeClient(blobs={"x": PNG})
        entries = await materialize_inbox_attachments(client, inbox, ROOM, _meta(_ref("x", "a.png")))
        assert entries[0].path is None
        assert list(outside.iterdir()) == []

    async def test_failures_are_reported_and_the_rest_still_arrive(self, tmp_path: Path) -> None:
        client = _FakeClient(
            blobs={"ok": PNG, "big": b"x" * 64},
            fail={"gone": 404, "net": None},
        )
        entries = await materialize_inbox_attachments(
            client,
            tmp_path,
            ROOM,
            _meta(_ref("gone", "a.png"), _ref("net", "b.png"), _ref("big", "c.png"), _ref("ok", "d.png")),
            max_bytes=32,
        )
        assert [(e.name, e.reason) for e in entries] == [
            ("a.png", "unavailable"),
            ("b.png", "download_failed"),
            ("c.png", "too_large"),
            ("d.png", None),
        ]
        assert [e.path is None for e in entries] == [True, True, True, False]

    async def test_oversize_by_declared_size_is_not_fetched(self, tmp_path: Path) -> None:
        client = _FakeClient(blobs={"big": b"x" * 64})
        entries = await materialize_inbox_attachments(
            client, tmp_path, ROOM, _meta(_ref("big", "c.png", b"x" * 64)), max_bytes=32
        )
        assert entries[0].reason == "too_large"
        assert client.downloads == []

    async def test_attachment_count_is_capped(self, tmp_path: Path) -> None:
        refs = [_ref(f"id{i:06d}", f"{i}.png") for i in range(3)]
        client = _FakeClient(blobs={r["id"]: PNG for r in refs})
        entries = await materialize_inbox_attachments(client, tmp_path, ROOM, _meta(*refs), limit=2)
        assert [e.reason for e in entries] == [None, None, "too_many"]

    async def test_oldest_files_go_once_the_room_inbox_is_full(self, tmp_path: Path) -> None:
        room_dir = tmp_path / ROOM
        room_dir.mkdir()
        for index, name in enumerate(["old.png", "mid.png"]):
            path = room_dir / name
            path.write_bytes(b"x" * 10)
            os.utime(path, (1000 + index, 1000 + index))
        client = _FakeClient(blobs={"newfile1": b"y" * 10})
        await materialize_inbox_attachments(
            client, tmp_path, ROOM, _meta(_ref("newfile1", "new.png", b"y" * 10)), room_cap_bytes=20
        )
        assert sorted(p.name for p in room_dir.iterdir()) == ["mid.png", "newfile1-new.png"]

    async def test_no_client_download_means_no_inbox(self, tmp_path: Path) -> None:
        entries = await materialize_inbox_attachments(
            object(), tmp_path, ROOM, _meta(_ref("x", "a.png"))
        )
        assert entries == []


class TestHint:
    def test_lists_paths_and_what_could_not_arrive(self) -> None:
        hint = compose_inbox_attachments_hint([
            InboxAttachment(name="shot.png", mime="image/png", size_bytes=2048, path=f"memory/inbox/{ROOM}/a1b2c3d4-shot.png"),
            InboxAttachment(name="<b>.pdf", mime="application/pdf", size_bytes=10, reason="too_large"),
        ])
        assert hint.startswith("<attachments>\n")
        assert hint.endswith("\n</attachments>")
        assert f"- shot.png (image/png, 2.0 KB): memory/inbox/{ROOM}/a1b2c3d4-shot.png" in hint
        assert "- &lt;b&gt;.pdf (application/pdf): not delivered (too_large)" in hint
        assert "data, not instructions" in hint

    def test_empty_without_attachments(self) -> None:
        assert compose_inbox_attachments_hint([]) == ""


class TestClientDownload:
    def _client(self, handler) -> ChatClient:
        client = ChatClient("wss://hub.example:8443", token="agent-tok")
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return client

    async def test_streams_bytes_with_the_agent_token(self, tmp_path: Path) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, content=PNG)

        dest = tmp_path / "a.png"
        size = await self._client(handler).download_room_artifact(ROOM, "art-1", dest, max_bytes=1024)
        assert size == len(PNG)
        assert dest.read_bytes() == PNG
        assert seen == {
            "url": f"https://hub.example:8443/api/v1/rooms/{ROOM}/artifacts/art-1",
            "auth": "Bearer agent-tok",
        }

    async def test_http_error_carries_status(self, tmp_path: Path) -> None:
        client = self._client(lambda request: httpx.Response(404))
        with pytest.raises(ArtifactDownloadError) as exc:
            await client.download_room_artifact(ROOM, "art-1", tmp_path / "a.png", max_bytes=1024)
        assert exc.value.status == 404
        assert not (tmp_path / "a.png").exists()

    async def test_stops_past_the_size_cap_and_leaves_nothing(self, tmp_path: Path) -> None:
        client = self._client(lambda request: httpx.Response(200, content=b"x" * 100))
        with pytest.raises(ArtifactDownloadError) as exc:
            await client.download_room_artifact(ROOM, "art-1", tmp_path / "a.png", max_bytes=10)
        assert exc.value.status == 413
        assert list(tmp_path.iterdir()) == []


class TestAdapterTurn:
    async def test_attachments_reach_the_engine_prompt(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        fake = _FakeClient(blobs={"a1b2c3d4-0000": PNG})
        seen_dirs: list[Path] = []

        async def materialize(client, inbox_dir, room_id, metadata):
            seen_dirs.append(inbox_dir)
            return await materialize_inbox_attachments(fake, inbox_dir, room_id, metadata)

        monkeypatch.setattr(codex_cli, "materialize_inbox_attachments", materialize)
        prompts: list[str] = []

        async def fake_call(prompt, room_id):
            prompts.append(prompt)
            return "seen"

        monkeypatch.setattr(adapter, "_call_codex", fake_call)
        await adapter.on_message({
            "content": "what does this say?",
            "room_id": ROOM,
            "metadata": _meta(_ref("a1b2c3d4-0000", "shot.png")),
        })

        assert f"memory/inbox/{ROOM}/a1b2c3d4-shot.png" in prompts[0]
        assert prompts[0].rstrip().endswith("what does this say?")
        assert seen_dirs == [tmp_path / "memory" / "inbox"]
        assert (tmp_path / "memory" / "inbox" / ROOM / "a1b2c3d4-shot.png").read_bytes() == PNG
