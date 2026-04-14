"""Engine auto-detection: discovers available agent engines on the machine."""

from __future__ import annotations

import asyncio
import os
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
    ("openhands", "openhands"),
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


# ── Python import-based detection ─────────────────────────────────────

PYTHON_ENGINES: list[tuple[str, str, str]] = [
    (
        "deepagents",
        "deepagents",
        'import deepagents; print(getattr(deepagents, "__version__", "unknown"))',
    ),
]


async def _detect_python_import(
    name: str, module: str, check_code: str
) -> EngineInfo | None:
    """Try to detect an engine via Python import."""
    python_path = shutil.which("python3") or shutil.which("python")
    if python_path is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            python_path,
            "-c",
            check_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=DETECTION_TIMEOUT
        )
        if proc.returncode != 0:
            return None
        version = stdout.decode().strip() or "unknown"
        return EngineInfo(engine=name, version=version, path=python_path)
    except (asyncio.TimeoutError, OSError) as exc:
        log.warning("python_detection_failed", engine=name, error=str(exc))
        return None


# ── Environment variable-based detection ──────────────────────────────

ENV_ENGINES: list[tuple[str, str]] = [
    ("openai", "OPENAI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
]


def _detect_env_var(name: str, env_var: str) -> EngineInfo | None:
    """Detect engine availability by checking environment variables."""
    value = os.environ.get(env_var)
    if not value:
        return None
    # Mask the key for the version field
    masked = value[:8] + "..." if len(value) > 8 else "***"
    return EngineInfo(engine=name, version=f"key={masked}", path=f"env:{env_var}")


# ── Public API ────────────────────────────────────────────────────────


async def detect_engines() -> DetectionResult:
    """Run all 6 engine detectors and return discovered engines.

    Detection sources:
      1-3. Binary detection: claude-code, codex, openhands
      4.   Python import: deepagents
      5-6. Env var: OPENAI_API_KEY, ANTHROPIC_API_KEY
    """
    result = DetectionResult()

    # Run binary and python detections concurrently
    tasks = []
    for name, binary in BINARY_ENGINES:
        tasks.append(_detect_binary(name, binary))
    for name, module, code in PYTHON_ENGINES:
        tasks.append(_detect_python_import(name, module, code))

    detected = await asyncio.gather(*tasks, return_exceptions=True)
    for item in detected:
        if isinstance(item, EngineInfo):
            result.engines.append(item)
        elif isinstance(item, Exception):
            log.warning("detection_error", error=str(item))

    # Env var detection is synchronous
    for name, env_var in ENV_ENGINES:
        info = _detect_env_var(name, env_var)
        if info is not None:
            result.engines.append(info)

    log.info("detection_complete", engine_count=len(result.engines))
    return result
