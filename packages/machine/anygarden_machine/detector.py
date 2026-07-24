"""Engine auto-detection: discovers available agent engines on the machine."""

from __future__ import annotations

import asyncio
import importlib
import shutil
from dataclasses import dataclass, field

import structlog

from anygarden_machine.engines.registry import ENGINE_LIFECYCLES

log = structlog.get_logger()

DETECTION_TIMEOUT = 5.0  # seconds


@dataclass
class EngineInfo:
    """Detected engine with version and path/source marker."""

    engine: str
    version: str
    path: str


@dataclass
class DetectionResult:
    """Aggregated detection results."""

    engines: list[EngineInfo] = field(default_factory=list)


# ── Binary-based detection ────────────────────────────────────────────

# #553 — derived from the engine lifecycle registry, the single source of
# truth for how each engine is detected. Binary engines run
# ``<binary> --version``; the on-disk name may differ from the engine key
# (claude-code ships the ``claude`` binary, not ``claude-code``).
BINARY_ENGINES: list[tuple[str, str]] = [
    (lc.engine, lc.detect.binary)
    for lc in ENGINE_LIFECYCLES.values()
    if lc.detect.mode == "binary" and lc.detect.binary
]


# ── Python-module-based detection (#357) ──────────────────────────────

# Issue #357 — engines that ship as in-process Python SDKs rather than
# CLI binaries can't be discovered with ``shutil.which``. The cluster's
# ``/api/v1/agents/engines/available`` endpoint reads from
# ``machine_engines`` (advertised by this detector), so an engine
# missing here silently fails to appear in the agent-creation UI even
# when the catalog has it. Python detection covers that gap by
# attempting to import the SDK module — present means "this machine
# can host the engine in-process".
#
# Each entry is ``(engine_name, import_path, version_attr)``. The
# version attr is read with ``getattr(..., default="unknown")`` so a
# minor SDK rev that drops ``__version__`` doesn't disable detection.
PYTHON_MODULE_ENGINES: list[tuple[str, str, str]] = [
    (lc.engine, lc.detect.import_path, lc.detect.version_attr)
    for lc in ENGINE_LIFECYCLES.values()
    if lc.detect.mode == "module" and lc.detect.import_path and lc.detect.version_attr
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


def _detect_python_module(
    name: str, import_path: str, version_attr: str
) -> EngineInfo | None:
    """Try to detect an in-process Python SDK engine via ``import``.

    Returns ``EngineInfo`` with the resolved module file path so the
    advertised entry is debuggable (``ls -la`` against the path tells
    an operator which venv served the import). ``ImportError`` is the
    expected miss path; any other exception during import is logged
    but still treated as "engine absent" to keep detector robustness
    on par with the binary path.
    """
    try:
        module = importlib.import_module(import_path)
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 — protect detector from bad imports
        log.warning(
            "python_module_detection_failed",
            engine=name,
            import_path=import_path,
            error=str(exc),
        )
        return None
    version = getattr(module, version_attr, None) or "unknown"
    if not isinstance(version, str):
        version = str(version)
    # ``__file__`` may be ``None`` for namespace packages; fall back
    # to the import path so the EngineInfo always carries something
    # locatable.
    path = getattr(module, "__file__", None) or import_path
    return EngineInfo(engine=name, version=version, path=path)


# ── Public API ────────────────────────────────────────────────────────


async def detect_engines() -> DetectionResult:
    """Run all engine detectors and return discovered engines.

    Detection sources:
      Binary detection: claude-code, codex, gemini-cli
      Python module detection (#357): openhands
    """
    result = DetectionResult()

    binary_tasks = [_detect_binary(name, binary) for name, binary in BINARY_ENGINES]
    detected = await asyncio.gather(*binary_tasks, return_exceptions=True)
    for item in detected:
        if isinstance(item, EngineInfo):
            result.engines.append(item)
        elif isinstance(item, Exception):
            log.warning("detection_error", error=str(item))

    # Python-module detection runs synchronously — ``importlib`` does
    # not benefit from gather concurrency and adding threads would
    # fight the import lock. Each call is sub-millisecond when the
    # module is cached and a few ms on cold-start.
    for name, import_path, version_attr in PYTHON_MODULE_ENGINES:
        info = _detect_python_module(name, import_path, version_attr)
        if info is not None:
            result.engines.append(info)

    log.info("detection_complete", engine_count=len(result.engines))
    return result
