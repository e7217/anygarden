"""Disk-sourced agent instructions (#908)."""

from __future__ import annotations

from pathlib import Path

import pytest

from anygarden.machine import instructions as ins


def test_split_and_compose_round_trip() -> None:
    text = ins.compose("# Role\n\nInfra.", "## Platform\nrules")
    assert ins.MANAGED_START in text and ins.MANAGED_END in text
    assert ins.split_user_area(text) == "# Role\n\nInfra."
    assert ins.split_user_area(ins.compose("", "x")) == ""


def test_split_keeps_text_after_the_block_and_drops_a_broken_block() -> None:
    whole = f"top\n{ins.MANAGED_START}\ngen\n{ins.MANAGED_END}\nbottom\n"
    assert ins.split_user_area(whole) == "top\n\nbottom"
    broken = f"top\n{ins.MANAGED_START}\ngenerated only\n"
    assert ins.split_user_area(broken) == "top"
    assert ins.split_user_area("a\r\nb\r\n") == "a\nb"


def test_collect_reads_user_area_and_skills(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(ins.compose("rules", "managed"))
    (tmp_path / "skills" / "deploy").mkdir(parents=True)
    (tmp_path / "skills" / "deploy" / "SKILL.md").write_text("# Deploy")
    (tmp_path / "skills" / "deploy" / "image.png").write_bytes(b"\x89PNG")
    (tmp_path / "skills" / "deploy" / "blob.md").write_bytes(b"\xff\xfe")
    (tmp_path / "outside.md").write_text("secret")
    (tmp_path / "skills" / "link.md").symlink_to(tmp_path / "outside.md")

    snapshot = ins.collect(tmp_path)

    assert snapshot.agents_md == "rules"
    assert snapshot.skills == {"skills/deploy/SKILL.md": "# Deploy"}
    assert ins.skill_bodies(snapshot) == {"skills/deploy/SKILL.md": "# Deploy"}


def test_collect_refuses_oversized_skills(tmp_path: Path) -> None:
    (tmp_path / "skills" / "big").mkdir(parents=True)
    (tmp_path / "skills" / "big" / "SKILL.md").write_text("x" * (ins.MAX_FILE_BYTES + 1))
    with pytest.raises(ins.SnapshotTooLarge):
        ins.collect(tmp_path)


def test_digest_ignores_the_managed_block(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(ins.compose("rules", "one"))
    first = ins.collect(tmp_path).digest()
    (tmp_path / "AGENTS.md").write_text(ins.compose("rules", "two"))
    assert ins.collect(tmp_path).digest() == first
    assert ins.InstructionSnapshot.of("rules\n", None).digest() == first


def test_install_replaces_skills_and_keeps_the_archive(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("old")
    (tmp_path / "skills" / "old").mkdir(parents=True)
    (tmp_path / "skills" / "old" / "SKILL.md").write_text("old skill")

    archived = ins.archive(tmp_path, "conflict")
    snapshot = ins.InstructionSnapshot.of("new", {"skills/new/SKILL.md": "new skill"})
    ins.install(tmp_path, snapshot, "managed")

    assert archived is not None
    assert (archived / "AGENTS.md").read_text() == "old"
    assert (archived / "skills" / "old" / "SKILL.md").read_text() == "old skill"
    assert ins.collect(tmp_path) == snapshot
    assert not (tmp_path / "skills" / "old").exists()


def test_receipt_round_trip(tmp_path: Path) -> None:
    assert ins.read_receipt(tmp_path) is None
    ins.write_receipt(tmp_path, ins.Receipt(revision=3, digest="abc"))
    assert ins.read_receipt(tmp_path) == ins.Receipt(revision=3, digest="abc")
    (tmp_path / ins.RECEIPT_NAME).write_text("not json")
    assert ins.read_receipt(tmp_path) is None
