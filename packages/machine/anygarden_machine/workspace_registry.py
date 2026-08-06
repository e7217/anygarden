"""Machine-local workspace registration and one-time consent.

The cluster only ever sees opaque workspace identifiers and redacted
descriptors.  Canonical host paths remain in this owner-readable local file.
This module deliberately does *not* expose an execution-root adapter: Phase 5
establishes the registration/consent boundary while external workspace writes
remain fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from anygarden_machine.safefs import safe_write_text, secure_chmod

DEFAULT_REGISTRY_PATH = Path.home() / ".anygarden" / "workspaces.json"
_DENIED_PARTS = frozenset({".git", ".env", ".ssh", ".aws", ".gnupg"})
_DENIED_NAMES = frozenset(
    {
        "auth.json",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        ".netrc",
    }
)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_CONSENT_PROOF = re.compile(r"^wcp_[0-9a-f]{64}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_proof(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_label(value: str) -> str:
    """Accept a human label while rejecting path-shaped disclosure."""

    label = value.strip()
    if not label:
        raise ValueError("workspace label cannot be empty")
    if (
        label.startswith(("/", "~", "\\\\"))
        or _WINDOWS_ABSOLUTE.match(label)
        or "/" in label
        or "\\" in label
    ):
        raise ValueError("workspace label cannot contain a host path")
    return label


def _path_has_symlink(path: Path) -> bool:
    """Check every existing component without following it."""

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def normalize_allowlist(paths: list[str]) -> list[str]:
    """Return a deterministic relative allowlist or raise ``ValueError``."""

    normalized: set[str] = set()
    for raw in paths:
        value = raw.strip().replace("\\", "/")
        if not value:
            raise ValueError("allowlist entries cannot be empty")
        candidate = Path(value)
        if candidate.is_absolute() or value.startswith("/"):
            raise ValueError("allowlist entries must be relative")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("allowlist traversal and empty segments are forbidden")
        lowered = {part.lower() for part in parts}
        if lowered & _DENIED_PARTS or parts[-1].lower() in _DENIED_NAMES:
            raise ValueError(
                "allowlist includes a protected repository or credential path"
            )
        normalized.add("/".join(parts))
    if not normalized:
        raise ValueError("at least one allowlist entry is required")
    return sorted(normalized)


def allowlist_hash(paths: list[str]) -> str:
    normalized = normalize_allowlist(paths)
    payload = json.dumps(normalized, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def repository_fingerprint(root: Path) -> str:
    """Hash stable git identity without revealing its values to the cluster."""

    def _git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip()

    top = Path(_git("rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise ValueError("workspace path must be the canonical git repository root")
    head = _git("rev-parse", "HEAD")
    try:
        remote = _git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        remote = ""
    return hashlib.sha256(f"git-v1\0{remote}\0{head}".encode("utf-8")).hexdigest()


class WorkspaceConsent(BaseModel):
    proof_hash: str
    agent_id: str
    room_id: str
    mode: Literal["read", "write"]
    expires_at: str
    used_at: str | None = None


class WorkspaceRegistration(BaseModel):
    workspace_id: str
    label: str = Field(min_length=1, max_length=80)
    canonical_root: str
    fingerprint: str
    allowlist: list[str]
    allowlist_hash: str
    max_mode: Literal["read", "write"]
    expires_at: str
    revoked_at: str | None = None
    consents: list[WorkspaceConsent] = Field(default_factory=list)

    def descriptor(self) -> dict[str, str]:
        """Redacted shape safe for machine registration frames."""

        return {
            "workspace_id": self.workspace_id,
            "label": self.label,
            "fingerprint": self.fingerprint,
            "allowlist_hash": self.allowlist_hash,
            "max_mode": self.max_mode,
            "expires_at": self.expires_at,
        }


class WorkspaceRegistry:
    """Owner-readable machine-local registry.

    The caller may inject a path for tests; production defaults to
    ``~/.anygarden/workspaces.json``.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_REGISTRY_PATH

    def _load(self) -> list[WorkspaceRegistration]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [WorkspaceRegistration.model_validate(row) for row in data]
        except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ValueError(f"invalid workspace registry: {exc}") from exc

    def _save(self, rows: list[WorkspaceRegistration]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        secure_chmod(self.path.parent, 0o700)
        safe_write_text(
            self.path,
            json.dumps(
                [row.model_dump() for row in rows],
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            mode=0o600,
        )

    def register(
        self,
        root: Path,
        *,
        label: str,
        max_mode: Literal["read", "write"],
        allowlist: list[str],
        expires_in: timedelta,
    ) -> WorkspaceRegistration:
        requested = root.expanduser().absolute()
        if _path_has_symlink(requested):
            raise ValueError("workspace root cannot contain symlink components")
        canonical = requested.resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("workspace root must be a directory")
        if expires_in <= timedelta(0):
            raise ValueError("workspace registration expiry must be in the future")
        normalized = normalize_allowlist(allowlist)
        row = WorkspaceRegistration(
            workspace_id=f"ws_{secrets.token_urlsafe(24)}",
            label=validate_label(label),
            canonical_root=str(canonical),
            fingerprint=repository_fingerprint(canonical),
            allowlist=normalized,
            allowlist_hash=allowlist_hash(normalized),
            max_mode=max_mode,
            expires_at=_iso(_utcnow() + expires_in),
        )
        rows = self._load()
        rows.append(row)
        self._save(rows)
        return row

    def list_descriptors(self) -> list[dict[str, str]]:
        now = _utcnow()
        return [
            row.descriptor()
            for row in self._load()
            if row.revoked_at is None and _parse_time(row.expires_at) > now
        ]

    def revoke(self, workspace_id: str) -> bool:
        rows = self._load()
        changed = False
        for row in rows:
            if row.workspace_id == workspace_id and row.revoked_at is None:
                row.revoked_at = _iso(_utcnow())
                changed = True
        if changed:
            self._save(rows)
        return changed

    def issue_consent(
        self,
        workspace_id: str,
        *,
        agent_id: str,
        room_id: str,
        mode: Literal["read", "write"],
        expires_in: timedelta,
    ) -> str:
        if expires_in <= timedelta(0):
            raise ValueError("consent expiry must be in the future")
        rows = self._load()
        now = _utcnow()
        # The raw random value is never returned or persisted.  Only its
        # one-way proof crosses HTTP/WebSocket, and that proof is still
        # single-use and scope-bound by the local registry.
        raw_consent = secrets.token_urlsafe(32)
        proof = f"wcp_{hashlib.sha256(raw_consent.encode('utf-8')).hexdigest()}"
        for row in rows:
            if row.workspace_id != workspace_id:
                continue
            if row.revoked_at is not None or _parse_time(row.expires_at) <= now:
                raise ValueError("workspace registration is revoked or expired")
            if mode == "write" and row.max_mode != "write":
                raise ValueError("workspace registration is read-only")
            row.consents.append(
                WorkspaceConsent(
                    proof_hash=_hash_proof(proof),
                    agent_id=agent_id,
                    room_id=room_id,
                    mode=mode,
                    expires_at=_iso(now + expires_in),
                )
            )
            self._save(rows)
            return proof
        raise KeyError(workspace_id)

    def verify_and_consume(
        self,
        *,
        workspace_id: str,
        agent_id: str,
        room_id: str,
        mode: Literal["read", "write"],
        fingerprint: str,
        allowlist_digest: str,
        consent_proof: str,
    ) -> tuple[bool, str, dict[str, str] | None]:
        """Verify local registration + scoped one-time consent.

        Returns ``(accepted, reason, descriptor)``.  A matching consent is
        consumed atomically before returning success; raw paths never leave
        this process.
        """

        rows = self._load()
        now = _utcnow()
        for row in rows:
            if row.workspace_id != workspace_id:
                continue
            reason: str | None = None
            if row.revoked_at is not None:
                reason = "workspace_revoked"
            elif _parse_time(row.expires_at) <= now:
                reason = "workspace_expired"
            elif row.fingerprint != fingerprint:
                reason = "fingerprint_mismatch"
            elif row.allowlist_hash != allowlist_digest:
                reason = "allowlist_mismatch"
            elif mode == "write" and row.max_mode != "write":
                reason = "mode_exceeds_local_policy"
            else:
                try:
                    local_root = Path(row.canonical_root)
                    if (
                        _path_has_symlink(local_root)
                        or local_root.resolve(strict=True) != local_root
                        or repository_fingerprint(local_root) != row.fingerprint
                    ):
                        reason = "fingerprint_mismatch"
                except (OSError, subprocess.SubprocessError, ValueError):
                    reason = "fingerprint_mismatch"
            if reason is not None:
                return False, reason, None

            if not _CONSENT_PROOF.fullmatch(consent_proof):
                return False, "consent_invalid", None
            proof_hash = _hash_proof(consent_proof)
            for consent in row.consents:
                if not secrets.compare_digest(consent.proof_hash, proof_hash):
                    continue
                if consent.used_at is not None:
                    return False, "consent_replayed", None
                if _parse_time(consent.expires_at) <= now:
                    return False, "consent_expired", None
                if (
                    consent.agent_id != agent_id
                    or consent.room_id != room_id
                    or consent.mode != mode
                ):
                    return False, "consent_scope_mismatch", None
                consent.used_at = _iso(now)
                self._save(rows)
                return True, "verified", row.descriptor()
            return False, "consent_invalid", None
        return False, "workspace_unknown", None
