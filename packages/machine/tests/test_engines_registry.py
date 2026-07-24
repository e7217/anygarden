"""Tests for the engine lifecycle registry (#553)."""

from __future__ import annotations

import pytest
from anygarden_machine.engines.registry import ENGINE_LIFECYCLES, get_lifecycle


def test_all_supported_engines_present():
    assert set(ENGINE_LIFECYCLES) == {
        "claude-code",
        "codex-cli",
        "gemini-cli",
        "openhands",
    }


@pytest.mark.parametrize(
    "engine,kind,package",
    [
        ("codex-cli", "npm", "@openai/codex"),
        ("gemini-cli", "npm", "@google/gemini-cli"),
        ("claude-code", "npm", "@anthropic-ai/claude-code"),
        ("openhands", "pip", "openhands-sdk"),
    ],
)
def test_channel_and_package(engine, kind, package):
    lc = get_lifecycle(engine)
    assert lc is not None
    assert lc.channel.kind == kind
    assert lc.package == package


def test_binary_detect_uses_on_disk_name():
    # claude-code ships the `claude` binary, not `claude-code`.
    assert get_lifecycle("claude-code").detect.binary == "claude"
    assert get_lifecycle("codex-cli").detect.mode == "binary"
    assert get_lifecycle("codex-cli").detect.binary == "codex"


def test_module_detect_spec():
    d = get_lifecycle("openhands").detect
    assert d.mode == "module"
    assert d.import_path == "openhands.sdk"
    assert d.version_attr == "__version__"


def test_unknown_engine_is_rejected():
    # None is the allowlist rejection signal for updates.
    assert get_lifecycle("does-not-exist") is None
