"""Static system info collection for machine registration (issue #523).

The daemon calls :func:`collect_system_info` once at connect time and ships
the result in the ``register`` frame. Every field is best-effort: a probe
that raises falls back to a safe default so a flaky ``psutil`` call or an
odd platform never blocks the machine from coming online.
"""

from __future__ import annotations

import platform
import socket
from typing import Callable, TypeVar

import psutil

from anygarden_machine.protocol.frames import SystemInfo

_GB = 1024**3

_T = TypeVar("_T")


def _safe(fn: Callable[[], _T], default: _T) -> _T:
    """Run ``fn`` and swallow any exception, returning ``default`` instead."""
    try:
        return fn()
    except Exception:
        return default


def _primary_lan_ip() -> str | None:
    """Return the primary LAN IPv4 — the source address of the default route.

    A UDP socket ``connect`` only makes the kernel pick a route and bind a
    local address; no packets are sent (UDP is connectionless). The target
    need not be reachable. Returns ``None`` if no route can be resolved.

    Socket *creation* itself is inside the guard: on a sandboxed host where
    AF_INET/SOCK_DGRAM is blocked (seccomp / network namespace) or under fd
    exhaustion, ``socket.socket`` raises ``OSError`` — we must swallow that
    too so collection stays best-effort and never blocks register.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return None
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def collect_system_info() -> SystemInfo:
    """Collect static system info. Each field degrades independently."""
    return SystemInfo(
        hostname=_safe(socket.gethostname, ""),
        # Wrapped in _safe for symmetry with the other fields: even though
        # _primary_lan_ip already guards OSError internally, this guarantees
        # no probe can ever propagate out of collection.
        lan_ip=_safe(_primary_lan_ip, None),
        os_platform=_safe(platform.platform, ""),
        cpu_cores=_safe(lambda: psutil.cpu_count(logical=True), 0) or 0,
        memory_gb=_safe(
            lambda: round(psutil.virtual_memory().total / _GB, 1), 0.0
        ),
    )
