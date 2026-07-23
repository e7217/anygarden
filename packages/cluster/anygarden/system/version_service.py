"""Local version resolution, PyPI latest lookup, and comparison (#546).

External I/O (the PyPI call) is isolated to ``fetch_pypi_latest`` and
never raises — a failure returns ``None`` so callers never block. This
keeps the "manual now, automatic later" split trivial: a background
poller can call the same functions and write the same cache.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

import httpx
import structlog
from packaging.version import InvalidVersion, parse

log = structlog.get_logger()

PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"
_FETCH_TIMEOUT = 5.0  # seconds — a stalled PyPI must not hang the request

# Sentinel returned when the package isn't installed (running from source).
DEV_VERSION = "0.0.0+dev"


def get_local_version(package: str) -> str:
    """Return the installed distribution version, or ``0.0.0+dev``.

    Sourced from distribution metadata (generated from pyproject.toml) so
    it can't drift from the real release, unlike a hardcoded string.
    """
    try:
        return _dist_version(package)
    except PackageNotFoundError:  # uninstalled source tree
        return DEV_VERSION


async def fetch_pypi_latest(
    package: str, *, client: httpx.AsyncClient | None = None
) -> str | None:
    """Return the latest version of ``package`` on PyPI, or ``None``.

    Any failure — network error, non-200, malformed payload — is
    swallowed and returned as ``None`` (logged at warning). The caller
    records the miss without blocking.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=_FETCH_TIMEOUT)
    try:
        resp = await client.get(PYPI_JSON_URL.format(package=package))
        if resp.status_code != 200:
            return None
        version = resp.json().get("info", {}).get("version")
        return version or None
    except (httpx.HTTPError, ValueError) as exc:  # ValueError ⇒ bad JSON
        log.warning("pypi_fetch_failed", package=package, error=str(exc))
        return None
    finally:
        if owns_client:
            await client.aclose()


def is_update_available(current: str, latest: str | None) -> bool:
    """True when ``latest`` is a strictly newer release than ``current``.

    Uses PEP 440 parsing (not string compare, which would order 0.10 <
    0.9). A local/dev ``current`` (e.g. ``0.0.0+dev``) suppresses the
    signal — a source checkout can't be meaningfully compared to a
    release.
    """
    if not latest:
        return False
    try:
        parsed_current = parse(current)
        parsed_latest = parse(latest)
    except InvalidVersion:
        return False
    if parsed_current.local is not None:  # source checkout, not a release
        return False
    return parsed_latest > parsed_current
