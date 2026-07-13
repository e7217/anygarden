"""Tests for the static system info collector (issue #523)."""

from __future__ import annotations

import anygarden_machine.sysinfo as sysinfo
from anygarden_machine.protocol.frames import SystemInfo


class _FakeVM:
    def __init__(self, total: int) -> None:
        self.total = total


def _patch_all(
    monkeypatch,
    *,
    hostname="h",
    platform="OS",
    cpu=4,
    total_bytes=1024**3,
    lan_ip=None,
):
    monkeypatch.setattr(sysinfo.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(sysinfo.platform, "platform", lambda: platform)
    monkeypatch.setattr(sysinfo.psutil, "cpu_count", lambda logical=True: cpu)
    monkeypatch.setattr(
        sysinfo.psutil, "virtual_memory", lambda: _FakeVM(total_bytes)
    )
    monkeypatch.setattr(sysinfo, "_primary_lan_ip", lambda: lan_ip)


def test_collect_system_info_maps_fields(monkeypatch):
    _patch_all(
        monkeypatch,
        hostname="worker01",
        platform="TestOS-1.0-x86_64",
        cpu=8,
        total_bytes=64 * 1024**3,
        lan_ip="192.168.1.42",
    )
    info = sysinfo.collect_system_info()
    assert isinstance(info, SystemInfo)
    assert info.hostname == "worker01"
    assert info.lan_ip == "192.168.1.42"
    assert info.os_platform == "TestOS-1.0-x86_64"
    assert info.cpu_cores == 8
    assert info.memory_gb == 64.0


def test_memory_gb_rounded_one_decimal(monkeypatch):
    _patch_all(monkeypatch, total_bytes=int(6.5 * 1024**3))
    info = sysinfo.collect_system_info()
    assert info.memory_gb == 6.5


def test_cpu_count_none_becomes_zero(monkeypatch):
    _patch_all(monkeypatch, cpu=None)
    info = sysinfo.collect_system_info()
    assert info.cpu_cores == 0


def test_collect_absorbs_field_failure(monkeypatch):
    """A field-level exception must not abort the whole collection."""
    _patch_all(monkeypatch, cpu=2)

    def boom():
        raise RuntimeError("no psutil")

    monkeypatch.setattr(sysinfo.psutil, "virtual_memory", boom)
    info = sysinfo.collect_system_info()
    assert info.memory_gb == 0.0  # failed field → default
    assert info.cpu_cores == 2  # other fields still collected


def test_primary_lan_ip_returns_none_on_oserror(monkeypatch):
    class _FakeSock:
        def connect(self, addr):
            raise OSError("no route")

        def getsockname(self):
            return ("1.2.3.4", 0)

        def close(self):
            pass

    monkeypatch.setattr(sysinfo.socket, "socket", lambda *a, **k: _FakeSock())
    assert sysinfo._primary_lan_ip() is None


def test_primary_lan_ip_happy(monkeypatch):
    class _FakeSock:
        def connect(self, addr):
            pass

        def getsockname(self):
            return ("10.0.0.5", 12345)

        def close(self):
            pass

    monkeypatch.setattr(sysinfo.socket, "socket", lambda *a, **k: _FakeSock())
    assert sysinfo._primary_lan_ip() == "10.0.0.5"


def test_primary_lan_ip_socket_creation_failure(monkeypatch):
    """Socket *creation* raising OSError (sandboxed host / fd exhaustion)
    must be swallowed, not propagated."""

    def boom(*a, **k):
        raise OSError("socket creation blocked")

    monkeypatch.setattr(sysinfo.socket, "socket", boom)
    assert sysinfo._primary_lan_ip() is None


def test_collect_never_propagates_lan_ip_failure(monkeypatch):
    """collect_system_info stays best-effort even if the LAN IP probe
    blows up entirely — it must never block register (issue #523)."""

    def boom(*a, **k):
        raise OSError("socket creation blocked")

    monkeypatch.setattr(sysinfo.socket, "socket", boom)
    monkeypatch.setattr(sysinfo.socket, "gethostname", lambda: "h")
    monkeypatch.setattr(sysinfo.platform, "platform", lambda: "OS")
    monkeypatch.setattr(sysinfo.psutil, "cpu_count", lambda logical=True: 4)
    monkeypatch.setattr(
        sysinfo.psutil, "virtual_memory", lambda: _FakeVM(1024**3)
    )
    info = sysinfo.collect_system_info()
    assert info.lan_ip is None
    assert info.hostname == "h"
    assert info.cpu_cores == 4
