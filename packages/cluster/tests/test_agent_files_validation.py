"""Server-side parity of the agent-manifest path validation.

This test file intentionally duplicates the cases in
``doorae-machine/tests/test_agent_dir.py``. The two copies must stay
equivalent — if one diverges, the other is wrong.
"""

from __future__ import annotations

import pytest

from doorae.agent_files import AgentFilePathError, validate_agent_file_path


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
            "AGENTS.md",
            "CLAUDE.md",
            "random.md",
            "skills.md",
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
        path = "skills/a/b/c/d/e/f/SKILL.md"
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)

    def test_path_too_long_rejected(self) -> None:
        path = "skills/" + ("a" * 600) + "/SKILL.md"
        with pytest.raises(AgentFilePathError):
            validate_agent_file_path(path)
