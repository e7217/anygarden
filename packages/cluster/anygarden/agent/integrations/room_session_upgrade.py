"""Bounded metadata-only compatibility check for old unscoped Codex sessions."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from uuid import UUID


def legacy_codex_session_matches(
    home: Path, handle: str, *, workspace: Path, model: str, provider: str | None
) -> bool:
    try:
        UUID(handle)
        if provider is None:
            config = home / "config.toml"
            selected = tomllib.loads(config.read_text()) if config.is_file() else {}
            provider = selected.get("model_provider", "openai")
        matches = list((home / "sessions").rglob(f"*-{handle}.jsonl"))
        if len(matches) != 1:
            return False
        path = matches[0]
        if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            return False
        metadata = None
        last_model = None
        with path.open() as stream:
            for line in stream:
                event = json.loads(line)
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                if event.get("type") == "session_meta":
                    metadata = payload
                elif event.get("type") == "turn_context":
                    last_model = payload.get("model")
        return bool(
            metadata
            and metadata.get("id") == handle
            and metadata.get("model_provider") == provider
            and metadata.get("cwd") == str(workspace)
            and last_model == model
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False
