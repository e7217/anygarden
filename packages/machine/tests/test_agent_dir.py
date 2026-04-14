"""Tests for agent directory path validation.

Shared whitelist rules between doorae-server (manifest writes) and
doorae-machine (materialization) to defend against path traversal,
clobber attacks, and accidental escapes from the managed tree.
"""

from __future__ import annotations

import pytest

from doorae_machine.agent_dir import (
    AgentFilePathError,
    validate_agent_file_path,
    validate_agent_id,
)


class TestAllowedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "skills/coder/SKILL.md",
            "skills/reviewer-v2/SKILL.md",
            "skills/my_skill/reference.md",
            ".codex/config.toml",
            ".claude/settings.json",
            ".claude/skills/helper/SKILL.md",
            ".gemini/settings.json",
            ".gemini/.env",
            ".openhands/microagents/kb.md",
            "skills/a/b/c/refs/deep.md",
        ],
    )
    def test_valid_paths_pass(self, path: str) -> None:
        validate_agent_file_path(path)  # must not raise


class TestRejectedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/absolute/skills/SKILL.md",
            "/skills/coder/SKILL.md",
            "skills/..",
            "skills/../escape.md",
            "skills/./SKILL.md",
            "..",
            "../outside.md",
            "skills//double/SKILL.md",
            "skills/\x00null/SKILL.md",
            "skills/\nnewline/SKILL.md",
        ],
    )
    def test_path_traversal_and_control_chars_rejected(self, path: str) -> None:
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            "workspace/anything.md",
            "workspace/nested/file.txt",
            "AGENTS.md",  # materializer writes this itself from agents_md field
            "CLAUDE.md",  # synthetic symlink, not manifest
            "random.md",  # not under an allowed prefix
            "skills.md",  # skills must be a dir, not a loose file
            "other/file.md",
        ],
    )
    def test_paths_outside_whitelist_rejected(self, path: str) -> None:
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            "skills/coder/run.sh",
            "skills/coder/payload.exe",
            "skills/coder/binary.so",
            ".codex/config.py",
        ],
    )
    def test_disallowed_extensions_rejected(self, path: str) -> None:
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)

    def test_path_too_deep_rejected(self) -> None:
        # 7 segments — deeper than the 6-level cap from the plan
        path = "skills/a/b/c/d/e/f/SKILL.md"
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)

    def test_path_too_long_rejected(self) -> None:
        # Extremely long path — defense against accidental DoS
        path = "skills/" + ("a" * 600) + "/SKILL.md"
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)


class TestValidateAgentId:
    """agent_id is used as a path segment under ~/.doorae/agents/, so
    a maliciously crafted value could escape the managed root. The
    rules enforce a narrow filename-like alphabet — UUIDs fit and so
    does any reasonable test id like ``agent-x``.
    """

    @pytest.mark.parametrize(
        "agent_id",
        [
            "0a6b8cfb-0d2c-42de-a568-1adfb2256169",  # UUID v4
            "agent-x",
            "agent-test-001",
            "A",
            "aGent_1",
            "0" * 64,  # boundary: max length
        ],
    )
    def test_valid_agent_ids(self, agent_id: str) -> None:
        validate_agent_id(agent_id)  # must not raise

    @pytest.mark.parametrize(
        "agent_id",
        [
            "",
            "..",
            "../escape",
            "../../etc",
            "/etc",
            "/",
            "a/b",
            "a\\b",
            ".hidden",
            "agent.with.dot",  # dots not allowed — keeps the alphabet narrow
            "agent id",  # space
            "agent\x00id",  # null
            "agent\nid",  # newline
            "a" * 65,  # over max length
        ],
    )
    def test_rejected_agent_ids(self, agent_id: str) -> None:
        with pytest.raises(AgentFilePathError):
            validate_agent_id(agent_id)
