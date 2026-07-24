"""Install-channel abstraction for engine CLIs (#553).

A :class:`Channel` encapsulates *how* one engine is queried and updated —
the per-registry knowledge (npm vs pip) behind a uniform interface so the
detector, latest-version check, and updater never branch per engine.

Channels are treated as **singular**: one engine maps to exactly one update
channel (see the design doc §4.5). A genuinely new install channel (docker,
brew, …) is added by implementing this Protocol once — the extension point —
without touching the registry or the update plumbing.

Security (#550 lineage): the package identifier is supplied by the machine's
own registry, never by the server. ``update_argv`` returns an argv list that
is executed without a shell, so no server-provided string can become a
package name, source, or shell fragment.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

import httpx
import structlog

log = structlog.get_logger()

# Registry lookups are best-effort and must never hang the daemon.
_HTTP_TIMEOUT = 5.0

# A version token: three-part core plus an optional pre-release/build/local
# suffix. Three parts (not two) avoids matching stray "python 3.11"-style
# noise in ``--version`` banners. Shared by both channels because the numeric
# core is identical across semver and PEP 440; comparison (which does differ)
# lives in the cluster, not here.
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")


def _first_version(raw: str) -> str | None:
    """Extract the first version-like token from ``raw``; ``None`` if absent."""
    if not raw:
        return None
    match = _VERSION_RE.search(raw)
    return match.group(0) if match else None


async def _get_json(url: str, client: httpx.AsyncClient | None) -> Any | None:
    """GET ``url`` and return parsed JSON, or ``None`` on any failure.

    ``client`` is injectable for tests. When omitted a short-lived client is
    created and closed here. Never raises — a registry hiccup must not crash
    or block the daemon.
    """
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:  # ValueError ⇒ bad JSON
        log.warning("registry_fetch_failed", url=url, error=str(exc))
        return None
    finally:
        if owns:
            await client.aclose()


class Channel(Protocol):
    """How an engine CLI is queried for its latest version and updated."""

    kind: str
    """Short channel tag, e.g. ``"npm"`` | ``"pip"``. Used for logging/telemetry."""

    async def latest_version(
        self, package: str, *, client: httpx.AsyncClient | None = None
    ) -> str | None:
        """Return the registry's latest version for ``package``.

        Best-effort: any network/parse failure returns ``None`` (never raises)
        so a check can't block or crash the daemon. ``client`` is injectable
        for tests; when omitted a short-lived client is created and closed.
        """
        ...

    def normalize(self, raw: str) -> str | None:
        """Reduce a raw ``--version`` / registry string to a comparable version.

        E.g. ``"claude 2.1.211"`` → ``"2.1.211"``. Returns ``None`` when no
        version-like token is present. The channel owns this because the
        version grammar (semver vs PEP 440) is channel-specific.
        """
        ...

    def update_argv(self, package: str, python: str | None = None) -> list[str]:
        """Build the argv that installs the latest ``package`` (no shell).

        ``python`` is the interpreter path for interpreter-scoped channels
        (pip); ignored by system-global channels (npm).
        """
        ...


class NpmGlobal:
    """npm global channel: ``npm i -g <pkg>@latest``; ``registry.npmjs.org``."""

    kind = "npm"
    _REGISTRY_URL = "https://registry.npmjs.org/{package}/latest"

    async def latest_version(
        self, package: str, *, client: httpx.AsyncClient | None = None
    ) -> str | None:
        data = await _get_json(self._REGISTRY_URL.format(package=package), client)
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        return version or None

    def normalize(self, raw: str) -> str | None:
        return _first_version(raw)

    def update_argv(self, package: str, python: str | None = None) -> list[str]:
        # System-global install; the interpreter path is irrelevant. ``@latest``
        # lets npm resolve the newest published version.
        return ["npm", "install", "-g", f"{package}@latest"]


class PipVenv:
    """pip channel: ``<python> -m pip install -U <pkg>``; PyPI JSON API.

    Interpreter-scoped: ``python`` must point at the venv that hosts the
    in-process SDK engine (e.g. the anygarden-agent venv for ``openhands``).
    """

    kind = "pip"
    _PYPI_URL = "https://pypi.org/pypi/{package}/json"

    async def latest_version(
        self, package: str, *, client: httpx.AsyncClient | None = None
    ) -> str | None:
        data = await _get_json(self._PYPI_URL.format(package=package), client)
        if not isinstance(data, dict):
            return None
        version = data.get("info", {}).get("version")
        return version or None

    def normalize(self, raw: str) -> str | None:
        return _first_version(raw)

    def update_argv(self, package: str, python: str | None = None) -> list[str]:
        if not python:
            raise ValueError(
                "PipVenv.update_argv requires a target interpreter path"
            )
        return [python, "-m", "pip", "install", "-U", package]
