"""Engine auto-detection: discovers available agent engines on the machine."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

DETECTION_TIMEOUT = 5.0  # seconds


@dataclass
class EngineInfo:
    """Detected engine with version and binary path."""

    engine: str
    version: str
    path: str


@dataclass
class DetectionResult:
    """Aggregated detection results."""

    engines: list[EngineInfo] = field(default_factory=list)


# ── Binary-based detection ────────────────────────────────────────────

BINARY_ENGINES: list[tuple[str, str]] = [
    # The Claude Code CLI binary is named ``claude`` (not
    # ``claude-code``) — the "code" is only in the docs and the
    # install package. Use the real on-disk name so ``shutil.which``
    # actually finds it.
    ("claude-code", "claude"),
    ("codex", "codex"),
    ("gemini-cli", "gemini"),
]


async def _detect_binary(name: str, binary: str) -> EngineInfo | None:
    """Try to detect an engine by running `<binary> --version`."""
    path = shutil.which(binary)
    if path is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=DETECTION_TIMEOUT
        )
        if proc.returncode != 0:
            return None
        version = stdout.decode().strip().split("\n")[0] if stdout else "unknown"
        return EngineInfo(engine=name, version=version, path=path)
    except (asyncio.TimeoutError, OSError) as exc:
        log.warning("binary_detection_failed", engine=name, error=str(exc))
        return None


# ── Public API ────────────────────────────────────────────────────────


async def detect_engines() -> DetectionResult:
    """Run all engine detectors and return discovered engines.

    Detection sources:
      Binary detection: claude-code, codex, gemini-cli
    """
    result = DetectionResult()

    tasks = [_detect_binary(name, binary) for name, binary in BINARY_ENGINES]

    detected = await asyncio.gather(*tasks, return_exceptions=True)
    for item in detected:
        if isinstance(item, EngineInfo):
            result.engines.append(item)
        elif isinstance(item, Exception):
            log.warning("detection_error", error=str(item))

    log.info("detection_complete", engine_count=len(result.engines))
    return result
