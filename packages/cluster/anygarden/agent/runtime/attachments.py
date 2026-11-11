"""Move turn files between the server and the agent directory.

Outbox (#854): attach files an agent wrote during a turn to that turn's reply.
Inbox (#874): download the files a message attaches before the engine runs.
Linked files (#1017): attach the local files a reply links to.

The agent drops files under ``memory/outbox/<room_id>/``. The supervisor
snapshots that directory before the engine runs and, once the turn has a
reply, uploads whatever is new or changed and references it from the reply's
``metadata.references``. Only regular files directly inside the room
directory count — the same shape the daemon's outbox poller accepts.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

import structlog

from anygarden.agent.client import ArtifactDownloadError, ArtifactUploadError

log = structlog.get_logger(__name__)

# Keeps one reply readable; extra files stay in the room's artifact panel.
REPLY_ATTACHMENT_LIMIT = 10

_UPLOAD_FAILURE_REASONS = {413: "too_large", 415: "unsupported_type"}


def _room_dir(outbox_dir: Path | None, room_id: str) -> Path | None:
    if outbox_dir is None:
        return None
    try:
        if str(UUID(room_id)) != room_id:
            return None
    except ValueError:
        return None
    room_dir = outbox_dir / room_id
    if not room_dir.is_dir() or room_dir.is_symlink():
        return None
    return room_dir


def _regular_files(room_dir: Path) -> list[Path]:
    return [
        entry
        for entry in sorted(room_dir.iterdir())
        if entry.is_file() and not entry.is_symlink()
    ]


def snapshot_outbox(outbox_dir: Path | None, room_id: str) -> dict[str, str]:
    """Return ``{filename: sha256}`` for the room's outbox files."""
    room_dir = _room_dir(outbox_dir, room_id)
    if room_dir is None:
        return {}
    snapshot: dict[str, str] = {}
    for entry in _regular_files(room_dir):
        try:
            snapshot[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest()
        except OSError:
            continue
    return snapshot


def changed_outbox_files(
    outbox_dir: Path | None, room_id: str, before: dict[str, str]
) -> list[Path]:
    """Files that are new or whose bytes changed since *before*."""
    after = snapshot_outbox(outbox_dir, room_id)
    room_dir = _room_dir(outbox_dir, room_id)
    if room_dir is None:
        return []
    return [room_dir / name for name, digest in after.items() if before.get(name) != digest]


async def _upload_one(client: Any, room_id: str, path: Path) -> tuple[str | None, str | None]:
    """Upload *path*; return ``(artifact_id, None)`` or ``(None, reason)``."""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        row = await client.upload_room_artifact(room_id, path, mime)
    except (ArtifactUploadError, OSError) as exc:
        status = getattr(exc, "status", None)
        log.warning(
            "reply_attachment_upload_failed",
            room_id=room_id,
            filename=path.name,
            status=status,
        )
        return None, _UPLOAD_FAILURE_REASONS.get(status, "upload_failed")
    return row["id"], None


async def upload_reply_attachments(
    client: Any,
    room_id: str,
    paths: list[Path],
    *,
    limit: int = REPLY_ATTACHMENT_LIMIT,
    uploaded: dict[Path, str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Upload *paths* and return ``(references, undelivered)``.

    A failed upload never blocks the reply; it is reported back as an
    ``undelivered`` entry so the user can see what is missing. When
    *uploaded* is given, each uploaded file's resolved path is recorded
    with its artifact id.
    """
    references: list[dict[str, str]] = []
    undelivered: list[dict[str, str]] = []
    for index, path in enumerate(paths):
        if index >= limit:
            undelivered.append({"name": path.name, "reason": "too_many"})
            continue
        artifact_id, reason = await _upload_one(client, room_id, path)
        if artifact_id is None:
            undelivered.append({"name": path.name, "reason": reason})
            continue
        references.append({"type": "artifact", "id": artifact_id})
        if uploaded is not None:
            uploaded[path.resolve()] = artifact_id
    return references, undelivered


# ── Linked files: local paths a reply links to (#1017) ──

# The server's default upload cap; checked first so a huge file is never read.
LINKED_FILE_MAX_BYTES = 20 * 1024 * 1024

_FENCED_CODE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.DOTALL)
_MARKDOWN_LINK = re.compile(
    r"\[[^\]\n]*\]\(\s*(?:<([^>\n]+)>|([^)\s]+))(?:\s+\"[^\"\n]*\")?\s*\)"
)
_AUTOLINK = re.compile(r"<([^>\s]+)>")
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")


def _is_local_href(href: str) -> bool:
    if not href or href.startswith(("#", "?", "~")):
        return False
    scheme = _SCHEME.match(href)
    return scheme is None or scheme.group(1).lower() == "file"


def extract_local_links(text: str) -> list[str]:
    """Local-path targets of markdown links and autolinks in *text*, in
    order and without duplicates. Code spans and fenced blocks are skipped
    — a path quoted as code is an example, not a deliverable."""
    prose = _INLINE_CODE.sub(" ", _FENCED_CODE.sub(" ", text))
    found: list[tuple[int, str]] = []
    for match in _MARKDOWN_LINK.finditer(prose):
        found.append((match.start(), match.group(1) or match.group(2)))
    for match in _AUTOLINK.finditer(prose):
        if _SCHEME.match(match.group(1)) or match.group(1).startswith("/"):
            found.append((match.start(), match.group(1)))
    links: list[str] = []
    for _, href in sorted(found):
        href = href.strip()
        if _is_local_href(href) and href not in links:
            links.append(href)
    return links


def _href_path(href: str) -> str:
    if _SCHEME.match(href):
        return unquote(urlsplit(href).path)
    return unquote(re.split(r"[?#]", href, maxsplit=1)[0])


def _is_output_file(path: Path, root: Path) -> bool:
    """*path* (already resolved) lies in the agent's output areas."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    if not parts or parts[0] not in ("workspace", "memory"):
        return False
    if any(part.startswith(".") for part in parts):
        return False
    if parts[0] == "memory" and len(parts) > 1 and parts[1] in ("inbox", "shared"):
        return False
    return path.is_file()


def resolve_linked_file(href: str, agent_root: Path) -> Path | None:
    """The file *href* names when it may be attached, else ``None``.

    Relative paths are tried against ``workspace/`` (the engines' working
    directory) and then the agent root. Only files in ``workspace/`` and
    ``memory/`` qualify — not the inbox or shared folders (files a person
    sent) and never through a hidden component — after symlinks resolve.
    The supervisor runs outside the engine sandbox, so this is what keeps
    a link from exposing the agent's credentials, logs or manifest.
    """
    if not _is_local_href(href):
        return None
    raw = _href_path(href)
    if not raw:
        return None
    root = agent_root.resolve()
    candidate = Path(raw)
    bases = [candidate] if candidate.is_absolute() else [
        agent_root / "workspace" / candidate,
        agent_root / candidate,
    ]
    for base in bases:
        try:
            resolved = base.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if _is_output_file(resolved, root):
            return resolved
    return None


async def upload_linked_files(
    client: Any,
    room_id: str,
    text: str,
    agent_root: Path,
    *,
    uploaded: dict[Path, str],
    limit: int,
    max_bytes: int = LINKED_FILE_MAX_BYTES,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Attach the files *text* links to; return ``(references, undelivered,
    linked)``, where ``linked`` maps each link to its artifact id.

    Files already in *uploaded* (this reply's outbox files, or an earlier
    link to the same file) are not uploaded again. Links that name no
    attachable file are left alone: the chat shows them as text.
    """
    references: list[dict[str, str]] = []
    undelivered: list[dict[str, str]] = []
    linked: list[dict[str, str]] = []
    refused: set[Path] = set()
    for href in extract_local_links(text):
        path = await asyncio.to_thread(resolve_linked_file, href, agent_root)
        if path is None or path in refused:
            continue
        artifact_id = uploaded.get(path)
        if artifact_id is None:
            reason = None
            if len(references) >= limit:
                reason = "too_many"
            elif path.stat().st_size > max_bytes:
                reason = "too_large"
            else:
                artifact_id, reason = await _upload_one(client, room_id, path)
            if artifact_id is None:
                refused.add(path)
                undelivered.append({"name": path.name, "reason": reason})
                continue
            uploaded[path] = artifact_id
            references.append({"type": "artifact", "id": artifact_id})
        linked.append({"href": href, "artifact_id": artifact_id})
    return references, undelivered, linked


# ── Inbox: files a message attaches, downloaded for the engine (#874) ──

# Mirrors the reply side: a message brings at most this many files.
INBOX_ATTACHMENT_LIMIT = REPLY_ATTACHMENT_LIMIT
# #1021 — the server's default upload cap, so a file a person could attach
# always reaches the agent.
INBOX_MAX_BYTES = 20 * 1024 * 1024
# Old attachments stay for follow-up questions until a room's inbox
# outgrows this; then the oldest go first.
INBOX_ROOM_CAP_BYTES = 100 * 1024 * 1024

_DOWNLOAD_FAILURE_REASONS = {
    413: "too_large",
    403: "unavailable",
    404: "unavailable",
    # #1018 — removed after its room stayed archived past retention.
    410: "expired",
}
_NAME_MAX_LEN = 120


@dataclass(frozen=True)
class InboxAttachment:
    """One attachment of the turn's message. ``path`` is relative to the
    agent directory, or ``None`` with a ``reason`` when it did not arrive."""

    name: str
    mime: str
    size_bytes: int
    path: str | None = None
    reason: str | None = None


def _safe_name(name: str) -> str:
    base = re.split(r"[\\/]", name)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    if base in ("", ".", ".."):
        return "attachment"
    return base[-_NAME_MAX_LEN:]


def _inbox_filename(artifact_id: str, name: str) -> str:
    """``<first 8 id chars>-<name>``: distinct files never collide, and the
    name is still readable for the engine (and in the prompt)."""
    prefix = re.sub(r"[^A-Za-z0-9]", "", artifact_id)[:8] or "file"
    return f"{prefix}-{_safe_name(name)}"


def _room_id_is_uuid(room_id: str) -> bool:
    try:
        return str(UUID(room_id)) == room_id
    except ValueError:
        return False


def _room_inbox(inbox_dir: Path, room_id: str) -> Path | None:
    """The room's inbox directory, created on demand; ``None`` when it is
    a symlink (an inbox must never write outside the agent directory)."""
    room_dir = inbox_dir / room_id
    if room_dir.is_symlink():
        return None
    room_dir.mkdir(parents=True, exist_ok=True)
    return room_dir


def _artifact_references(metadata: dict[str, Any] | None, room_id: str) -> list[dict[str, Any]]:
    raw = (metadata or {}).get("references")
    if not isinstance(raw, list):
        return []
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in raw:
        if not isinstance(ref, dict) or ref.get("type") != "artifact":
            continue
        artifact_id, name = ref.get("id"), ref.get("name")
        if not isinstance(artifact_id, str) or not artifact_id or not isinstance(name, str):
            continue
        if ref.get("room_id") != room_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        refs.append(ref)
    return refs


def _matches(path: Path, sha256: Any) -> bool:
    if not isinstance(sha256, str) or not path.is_file() or path.is_symlink():
        return False
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    except OSError:
        return False


def _prune(room_dir: Path, *, keep: set[str], cap_bytes: int) -> None:
    files = [
        (entry.stat().st_mtime, entry)
        for entry in _regular_files(room_dir)
        if not entry.name.startswith(".")
    ]
    total = sum(entry.stat().st_size for _, entry in files)
    for _, entry in sorted(files, key=lambda item: item[0]):
        if total <= cap_bytes:
            return
        if entry.name in keep:
            continue
        total -= entry.stat().st_size
        entry.unlink(missing_ok=True)


async def materialize_inbox_attachments(
    client: Any,
    inbox_dir: Path,
    room_id: str,
    metadata: dict[str, Any] | None,
    *,
    limit: int = INBOX_ATTACHMENT_LIMIT,
    max_bytes: int = INBOX_MAX_BYTES,
    room_cap_bytes: int = INBOX_ROOM_CAP_BYTES,
) -> list[InboxAttachment]:
    """Download the message's ``artifact`` references into
    ``<inbox_dir>/<room_id>/`` and describe each for the turn prompt.

    A failed download never blocks the turn — it comes back with a
    ``reason`` so the engine is told the file is missing instead of
    guessing at it.
    """
    download = getattr(client, "download_room_artifact", None)
    refs = _artifact_references(metadata, room_id)
    if download is None or not refs:
        return []
    if not _room_id_is_uuid(room_id):
        return []
    room_dir = await asyncio.to_thread(_room_inbox, inbox_dir, room_id)

    entries: list[InboxAttachment] = []
    kept: set[str] = set()
    for index, ref in enumerate(refs):
        name = ref["name"]
        mime = ref.get("mime") if isinstance(ref.get("mime"), str) else "application/octet-stream"
        size = ref.get("size_bytes") if isinstance(ref.get("size_bytes"), int) else 0

        def missing(reason: str) -> InboxAttachment:
            return InboxAttachment(name=name, mime=mime, size_bytes=size, reason=reason)

        if index >= limit:
            entries.append(missing("too_many"))
            continue
        if size > max_bytes:
            entries.append(missing("too_large"))
            continue
        if room_dir is None:
            entries.append(missing("download_failed"))
            continue
        filename = _inbox_filename(ref["id"], name)
        target = room_dir / filename
        if not await asyncio.to_thread(_matches, target, ref.get("sha256")):
            try:
                size = await download(room_id, ref["id"], target, max_bytes=max_bytes)
            except ArtifactDownloadError as exc:
                reason = _DOWNLOAD_FAILURE_REASONS.get(exc.status, "download_failed")
                log.warning(
                    "inbox_attachment_download_failed",
                    room_id=room_id,
                    artifact_id=ref["id"],
                    status=exc.status,
                )
                entries.append(missing(reason))
                continue
            except OSError:
                entries.append(missing("download_failed"))
                continue
        kept.add(filename)
        entries.append(
            InboxAttachment(
                name=name,
                mime=mime,
                size_bytes=size,
                path=f"memory/inbox/{room_id}/{filename}",
            )
        )
    if room_dir is not None:
        await asyncio.to_thread(_prune, room_dir, keep=kept, cap_bytes=room_cap_bytes)
    return entries
