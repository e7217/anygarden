"""Local manifest persistence layer for doorae-machine.

Each agent's desired-state manifest is saved to:
    <agents_root>/<agent_id>/manifest.json

The file is owner-readable only (mode 0o600) and contains all fields
from SyncDesiredStateFrame except ``type`` (a Pydantic discriminator
with no operational value on disk) and ``engine_secrets`` (sensitive
credentials that must not be persisted to disk). A ``saved_at`` ISO
8601 timestamp is added on every write.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from doorae_machine.protocol.frames import SyncDesiredStateFrame

logger = logging.getLogger(__name__)

# Fields stripped before writing to disk.
_EXCLUDED_FIELDS: frozenset[str] = frozenset({"type", "engine_secrets"})


class ManifestStore:
    """Persist and retrieve per-agent manifests on the local filesystem.

    Parameters
    ----------
    agents_root:
        Root directory that contains one subdirectory per agent
        (``<agents_root>/<agent_id>/manifest.json``).
        Defaults to ``~/.doorae/agents``.
    """

    def __init__(self, agents_root: Path | None = None) -> None:
        self.agents_root: Path = (
            agents_root if agents_root is not None else Path.home() / ".doorae" / "agents"
        )

    # ── Write operations ─────────────────────────────────────────────

    def save(self, frame: SyncDesiredStateFrame) -> Path:
        """Persist *frame* to ``<agents_root>/<agent_id>/manifest.json``.

        The file is created with mode 0o600 (owner read/write only).
        ``type`` and ``engine_secrets`` are excluded. ``saved_at`` (UTC
        ISO 8601) is added.

        Returns the path to the written file.
        """
        agent_dir = self.agents_root / frame.agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = agent_dir / "manifest.json"

        data = self._frame_to_dict(frame)
        data["saved_at"] = datetime.now(tz=timezone.utc).isoformat()

        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Enforce 0o600 regardless of umask
        manifest_path.chmod(0o600)

        return manifest_path

    def delete(self, agent_id: str) -> None:
        """Remove ``<agents_root>/<agent_id>/manifest.json``.

        Does **not** remove ``workspace/`` or the agent directory itself.
        A no-op if the manifest does not exist.
        """
        manifest_path = self.agents_root / agent_id / "manifest.json"
        try:
            manifest_path.unlink()
        except FileNotFoundError:
            pass

    def update_desired_state(self, agent_id: str, desired_state: str) -> None:
        """Update only the ``desired_state`` field of an existing manifest.

        Raises
        ------
        FileNotFoundError
            If no manifest exists for *agent_id*.
        """
        manifest_path = self.agents_root / agent_id / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"No manifest found for agent {agent_id!r} at {manifest_path}"
            )

        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["desired_state"] = desired_state
        data["saved_at"] = datetime.now(tz=timezone.utc).isoformat()

        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        manifest_path.chmod(0o600)

    # ── Read operations ──────────────────────────────────────────────

    def load(self, agent_id: str) -> SyncDesiredStateFrame | None:
        """Read and return the manifest for *agent_id*.

        Returns ``None`` if the manifest file is absent, contains invalid
        JSON, or does not conform to the SyncDesiredStateFrame schema.
        """
        manifest_path = self.agents_root / agent_id / "manifest.json"
        return self._read_manifest(manifest_path)

    def load_all_running(self) -> list[SyncDesiredStateFrame]:
        """Return all manifests whose ``desired_state`` is ``"running"``.

        Corrupt or missing manifests are skipped silently.
        """
        if not self.agents_root.exists():
            return []

        results: list[SyncDesiredStateFrame] = []
        for agent_dir in self.agents_root.iterdir():
            if not agent_dir.is_dir():
                continue
            manifest_path = agent_dir / "manifest.json"
            frame = self._read_manifest(manifest_path)
            if frame is not None and frame.desired_state == "running":
                results.append(frame)

        return results

    # ── Private helpers ──────────────────────────────────────────────

    def _frame_to_dict(self, frame: SyncDesiredStateFrame) -> dict[str, Any]:
        """Serialise *frame* to a plain dict, stripping excluded fields."""
        data = frame.model_dump()
        for field in _EXCLUDED_FIELDS:
            data.pop(field, None)
        return data

    def _read_manifest(self, path: Path) -> SyncDesiredStateFrame | None:
        """Parse *path* as a SyncDesiredStateFrame; return None on any error."""
        if not path.exists():
            return None

        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read manifest %s: %s", path, exc)
            return None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Corrupt manifest JSON at %s: %s", path, exc)
            return None

        # saved_at is not a SyncDesiredStateFrame field — drop it before
        # validation so Pydantic does not complain about extra fields.
        data.pop("saved_at", None)

        # Restore the discriminator field that was excluded on save.
        data.setdefault("type", "sync_desired_state")
        # engine_secrets defaults to {} if absent (excluded on save).
        data.setdefault("engine_secrets", {})

        try:
            return SyncDesiredStateFrame.model_validate(data)
        except ValidationError as exc:
            logger.warning("Invalid manifest schema at %s: %s", path, exc)
            return None
