"""Tests for the engine lifecycle registry (#553)."""

from __future__ import annotations

import pytest
from anygarden_machine.engines.registry import ENGINE_LIFECYCLES, get_lifecycle


def test_all_supported_engines_present():
    assert set(ENGINE_LIFECYCLES) == {
        "codex-cli",
        "pi-cli",
    }


@pytest.mark.parametrize(
    "engine,kind,package",
    [
        ("codex-cli", "npm", "@openai/codex"),
        ("pi-cli", "npm-managed", "@earendil-works/pi-coding-agent"),
    ],
)
def test_channel_and_package(engine, kind, package):
    lc = get_lifecycle(engine)
    assert lc is not None
    assert lc.channel.kind == kind
    assert lc.package == package


def test_binary_detect_uses_on_disk_name():
    # claude-code ships the `claude` binary, not `claude-code`.
    assert get_lifecycle("codex-cli").detect.mode == "binary"
    assert get_lifecycle("codex-cli").detect.binary == "codex"




def test_unknown_engine_is_rejected():
    # None is the allowlist rejection signal for updates.
    assert get_lifecycle("does-not-exist") is None
