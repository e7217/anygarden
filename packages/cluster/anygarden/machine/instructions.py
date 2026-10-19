"""Disk-sourced agent instructions (#908).

The machine's disk is the source of truth for an agent's instructions:
the user part of ``AGENTS.md`` and the files under ``skills/``. The server
keeps a snapshot (``agents.agents_md`` + ``agent_files`` rows under
``skills/``) so the instructions survive an offline machine and follow the
agent to a new one.

``AGENTS.md`` is split in two. Everything outside the managed block is the
user area, which people and the agent edit freely. The managed block
carries the platform sections and the inlined skills; the materializer
rewrites only that block on every start.

A receipt next to ``AGENTS.md`` records which server revision the disk was
last synced with and the digest of that synced content. Comparing the
current digest with the receipt tells the daemon whether there are local
edits to report, and comparing revisions tells the materializer whether
the server holds a newer snapshot (a restore, or a move back to a machine
with stale files).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from anygarden.machine.agent_dir import AgentFilePathError, validate_agent_file_path
from anygarden.machine.safefs import safe_write_text, secure_chmod

CAPABILITY = "agent_instructions_disk_v1"

AGENTS_MD = "AGENTS.md"
SKILLS_DIR = "skills"
RECEIPT_NAME = ".anygarden-instructions.json"
ARCHIVE_DIR = ".anygarden-archive"

MANAGED_START = (
    "<!-- anygarden:managed:start — generated on every start; "
    "edits inside this block are replaced -->"
)
MANAGED_END = "<!-- anygarden:managed:end -->"

# A snapshot travels in one machine WebSocket frame (1 MiB envelope).
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 640 * 1024
MAX_SKILL_FILES = 200


class SnapshotTooLarge(ValueError):
    """The instructions on disk exceed what one sync frame may carry."""


@dataclass(frozen=True)
class InstructionSnapshot:
    """The user area of ``AGENTS.md`` and the text files under ``skills/``."""

    agents_md: str = ""
    skills: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, agents_md: str | None, skills: dict[str, str] | None) -> InstructionSnapshot:
        return cls(
            agents_md=normalise_user_area(agents_md or ""),
            skills={path: body for path, body in sorted((skills or {}).items())},
        )

    def digest(self) -> str:
        body = json.dumps(
            {"agents_md": self.agents_md, "skills": self.skills},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Receipt:
    revision: int
    digest: str


# ── AGENTS.md ────────────────────────────────────────────────────────


def normalise_user_area(text: str) -> str:
    return text.replace("\r\n", "\n").strip("\n").rstrip()


def split_user_area(text: str) -> str:
    """Return ``text`` without the managed block.

    A start marker without an end marker drops everything after it: a
    half-deleted block must not leak generated text into the user area.
    """
    text = text.replace("\r\n", "\n")
    start = text.find(MANAGED_START)
    if start < 0:
        return normalise_user_area(text)
    end = text.find(MANAGED_END, start)
    tail = "" if end < 0 else text[end + len(MANAGED_END):]
    return normalise_user_area(text[:start] + tail)


def compose(user_area: str, managed: str) -> str:
    """Join the user area and a freshly rendered managed block."""
    user = normalise_user_area(user_area)
    block = f"{MANAGED_START}\n{managed.strip()}\n{MANAGED_END}\n"
    return f"{user}\n\n{block}" if user else block


def _read_text(path: Path, limit: int) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    with open(path, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise SnapshotTooLarge(f"{path.name} is larger than {limit} bytes")
    return data.decode("utf-8")


def read_agents_md(agent_root: Path) -> str | None:
    return _read_text(agent_root / AGENTS_MD, MAX_TOTAL_BYTES)


# ── Snapshot ─────────────────────────────────────────────────────────


def collect(agent_root: Path) -> InstructionSnapshot:
    """Read the instructions currently on disk.

    Symlinks are never followed. Skill files that are not valid manifest
    paths or not UTF-8 text are left out of the snapshot (they stay on
    disk); oversized content raises :class:`SnapshotTooLarge`.
    """
    agents_md = split_user_area(read_agents_md(agent_root) or "")
    total = len(agents_md.encode("utf-8"))
    skills: dict[str, str] = {}
    root = agent_root / SKILLS_DIR
    if root.is_dir() and not root.is_symlink():
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            dirnames[:] = sorted(
                name for name in dirnames if not (base / name).is_symlink()
            )
            for name in sorted(filenames):
                path = base / name
                rel = path.relative_to(agent_root).as_posix()
                try:
                    validate_agent_file_path(rel)
                except AgentFilePathError:
                    continue
                try:
                    body = _read_text(path, MAX_FILE_BYTES)
                except UnicodeDecodeError:
                    continue
                if body is None:
                    continue
                skills[rel] = body
                total += len(body.encode("utf-8"))
                if len(skills) > MAX_SKILL_FILES or total > MAX_TOTAL_BYTES:
                    raise SnapshotTooLarge("skills exceed the sync limit")
    return InstructionSnapshot.of(agents_md, skills)


def skill_bodies(snapshot: InstructionSnapshot) -> dict[str, str]:
    """``skills/<name>/SKILL.md`` bodies, the input of the managed block."""
    return {
        path: body
        for path, body in snapshot.skills.items()
        if path.startswith("skills/") and path.endswith("/SKILL.md")
    }


def read_skill_bodies(agent_root: Path) -> dict[str, str]:
    """``skills/<name>/SKILL.md`` bodies on disk, for the managed block.

    Unlike :func:`collect` this never fails: an unreadable or oversized
    skill is simply not inlined.
    """
    root = agent_root / SKILLS_DIR
    bodies: dict[str, str] = {}
    if not root.is_dir() or root.is_symlink():
        return bodies
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        try:
            body = _read_text(entry / "SKILL.md", MAX_FILE_BYTES)
        except (SnapshotTooLarge, UnicodeDecodeError, OSError):
            continue
        if body is not None:
            bodies[f"{SKILLS_DIR}/{entry.name}/SKILL.md"] = body
    return bodies


# ── Receipt ──────────────────────────────────────────────────────────


def read_receipt(agent_root: Path) -> Receipt | None:
    try:
        raw = _read_text(agent_root / RECEIPT_NAME, 4096)
    except (SnapshotTooLarge, UnicodeDecodeError):
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return Receipt(revision=int(data["revision"]), digest=str(data["digest"]))
    except (ValueError, KeyError, TypeError):
        return None


def write_receipt(agent_root: Path, receipt: Receipt) -> None:
    safe_write_text(
        agent_root / RECEIPT_NAME,
        json.dumps({"revision": receipt.revision, "digest": receipt.digest}),
        mode=0o600,
    )


# ── Install ──────────────────────────────────────────────────────────


def archive(agent_root: Path, reason: str) -> Path | None:
    """Move the current ``AGENTS.md`` and ``skills/`` aside, keeping them."""
    sources = [
        path
        for path in (agent_root / AGENTS_MD, agent_root / SKILLS_DIR)
        if path.exists() and not path.is_symlink()
    ]
    if not sources:
        return None
    target = agent_root / ARCHIVE_DIR / f"instructions-{reason}-{time.time_ns()}"
    target.mkdir(parents=True, exist_ok=False)
    secure_chmod(target.parent, 0o700)
    secure_chmod(target, 0o700)
    for source in sources:
        if source.is_dir():
            shutil.copytree(source, target / source.name, symlinks=True)
        else:
            shutil.copy2(source, target / source.name, follow_symlinks=False)
    return target


def write_skills(agent_root: Path, skills: dict[str, str]) -> None:
    """Make ``skills/`` hold exactly ``skills``; anything else there is removed."""
    root = agent_root / SKILLS_DIR
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("skills is not a safe directory")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    secure_chmod(root, 0o700)
    for rel, body in skills.items():
        validate_agent_file_path(rel)
        if not rel.startswith(f"{SKILLS_DIR}/"):
            raise AgentFilePathError(f"{rel!r} is not under skills/")
        target = agent_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        secure_chmod(target.parent, 0o700)
        safe_write_text(target, body, mode=0o600)


def install(agent_root: Path, snapshot: InstructionSnapshot, managed: str) -> None:
    """Write ``snapshot`` to disk: the user area plus the given managed block."""
    write_skills(agent_root, snapshot.skills)
    safe_write_text(agent_root / AGENTS_MD, compose(snapshot.agents_md, managed), mode=0o600)
