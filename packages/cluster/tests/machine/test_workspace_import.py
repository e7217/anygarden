"""#915 — unpacking moved work files on the new machine."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from anygarden.machine import managed_workspace
from anygarden.machine import workspace_import as wi

pytestmark = pytest.mark.skipif(not managed_workspace.supported(), reason="POSIX descriptor boundary")


def _zip(path: Path, entries: dict[str, bytes], *, links: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
        for name, target in (links or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return path


@pytest.mark.req("AGT-12")
def test_extracts_into_the_work_directory_and_skips_existing_names(tmp_path):
    target = tmp_path / "work"
    (target / "out").mkdir(parents=True)
    (target / "out" / "keep.md").write_text("already here")
    archive = _zip(tmp_path / "w.zip", {"out/keep.md": b"incoming", "out/new.md": b"new", "deep/a/b.txt": b"b"})

    with wi.open_target(target) as fd:
        assert wi.conflicts(archive, fd) == ["out/keep.md"]
        result = wi.extract(archive, fd, max_entries=10, max_bytes=100)

    assert result == wi.ImportResult(files=2, skipped=["out/keep.md"])
    assert (target / "out" / "keep.md").read_text() == "already here"
    assert (target / "out" / "new.md").read_text() == "new"
    assert (target / "deep" / "a" / "b.txt").read_text() == "b"


@pytest.mark.parametrize("name", ["../escape.txt", "/abs.txt", "a/../../x", "a\\b", ".env", "AGENTS.md", "skills/x/SKILL.md"])
def test_unsafe_member_names_refuse_the_whole_import(tmp_path, name):
    target = tmp_path / "work"
    target.mkdir()
    archive = _zip(tmp_path / "w.zip", {"ok.txt": b"ok", name: b"bad"})
    with pytest.raises(wi.ImportRefused), wi.open_target(target) as fd:
        wi.extract(archive, fd, max_entries=10, max_bytes=100)
    assert not (tmp_path / "escape.txt").exists()
    assert list(target.iterdir()) == []


def test_links_and_size_lies_are_refused(tmp_path):
    target = tmp_path / "work"
    target.mkdir()
    linked = _zip(tmp_path / "l.zip", {}, links={"link": "/etc/passwd"})
    with pytest.raises(wi.ImportRefused), wi.open_target(target) as fd:
        wi.extract(linked, fd, max_entries=10, max_bytes=100)
    big = _zip(tmp_path / "b.zip", {"a.bin": b"x" * 50, "b.bin": b"y" * 60})
    with pytest.raises(wi.ImportRefused), wi.open_target(target) as fd:
        wi.extract(big, fd, max_entries=10, max_bytes=100)
    with pytest.raises(wi.ImportRefused), wi.open_target(target) as fd:
        wi.extract(big, fd, max_entries=1, max_bytes=1000)
    assert list(target.iterdir()) == []


def test_a_file_where_a_folder_is_needed_is_skipped(tmp_path):
    target = tmp_path / "work"
    target.mkdir()
    (target / "out").write_text("a file, not a folder")
    archive = _zip(tmp_path / "w.zip", {"out/a.md": b"a", "top.md": b"t"})
    with wi.open_target(target) as fd:
        result = wi.extract(archive, fd, max_entries=10, max_bytes=100)
    assert result == wi.ImportResult(files=1, skipped=["out/a.md"])


def test_target_must_not_be_a_link(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "work").symlink_to(real)
    with pytest.raises(wi.ImportRefused), wi.open_target(tmp_path / "work"):
        pass


def test_work_directory_follows_the_engine():
    assert wi.work_dir_name("codex-cli") == "workspace"
    assert wi.work_dir_name("pi-cli") == "."


@pytest.mark.req("AGT-12")
@pytest.mark.parametrize("engine, work", [("codex-cli", "workspace"), ("pi-cli", ".")])
def test_materialize_unpacks_a_pending_import(tmp_path, engine, work):
    from unittest.mock import AsyncMock

    from anygarden.machine.spawner import SpawnManifest, Spawner

    spawner = Spawner(on_stopped=AsyncMock(), on_crashed=AsyncMock(), agent_dirs_root=tmp_path / "agents")
    root = tmp_path / "agents" / "agent-m"
    (root / wi.IMPORT_DIR).mkdir(parents=True)
    _zip(wi.import_zip_path(root), {"reports/r.md": b"moved"})

    spawner._materialize_agent_dir(SpawnManifest(agent_id="agent-m", engine=engine, agent_token="t", rooms=[]))

    assert (root / work / "reports" / "r.md").read_text() == "moved"
    assert not wi.import_zip_path(root).exists()


def test_a_broken_import_does_not_block_the_start(tmp_path):
    from unittest.mock import AsyncMock

    from anygarden.machine.spawner import SpawnManifest, Spawner

    spawner = Spawner(on_stopped=AsyncMock(), on_crashed=AsyncMock(), agent_dirs_root=tmp_path / "agents")
    root = tmp_path / "agents" / "agent-m"
    (root / wi.IMPORT_DIR).mkdir(parents=True)
    _zip(wi.import_zip_path(root), {"../escape": b"x"})

    spawner._materialize_agent_dir(SpawnManifest(agent_id="agent-m", engine="pi-cli", agent_token="t", rooms=[]))

    kept = list((root / wi.IMPORT_DIR).iterdir())
    assert len(kept) == 1 and kept[0].name.startswith("failed-")
    assert not (root.parent / "escape").exists()
