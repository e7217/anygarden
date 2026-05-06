"""Unit tests for the claude-code permission-level → settings.json
mapping (#309 PR-B).

Claude-code's permission surface is a JSON allow-list under
``permissions.allow`` plus a deny-list for materializer-managed files
that now live in the agent cwd. Restricted agents lose
Bash/Write/Edit/Task so the LLM can still inspect
(Read/Glob/Grep/WebSearch/WebFetch) but not execute shell commands or
mutate files. Standard and trusted both keep the pre-#309 broad
allow-list because claude-code has no OS-level sandbox — Bash already
lets the agent escape the cwd, so there is no separate ``trusted``
dial to relax.
"""

from __future__ import annotations

import json

import pytest

from doorae_machine.spawner import Spawner


class TestClaudeCodeDefaultSettings:
    def _allow_list(self, body: str) -> list[str]:
        return json.loads(body)["permissions"]["allow"]

    def _deny_list(self, body: str) -> list[str]:
        return json.loads(body)["permissions"]["deny"]

    def test_none_falls_back_to_standard(self) -> None:
        body = Spawner._claude_code_default_settings(None)
        allow = self._allow_list(body)
        assert "Bash" in allow
        assert "Write" in allow
        assert "Read" in allow

    def test_standard_matches_pre_309(self) -> None:
        body = Spawner._claude_code_default_settings("standard")
        allow = self._allow_list(body)
        # The 11-tool pre-#309 set, byte-for-byte (the order matters
        # because the JSON file is shipped verbatim — admin scripts
        # that diff settings.json should not see drift on upgrade).
        assert allow == [
            "WebSearch", "WebFetch", "Bash", "Read", "Write",
            "Edit", "Glob", "Grep", "Task", "TodoWrite",
        ]

    @pytest.mark.parametrize("tier", ["standard", "trusted", "restricted"])
    def test_all_tiers_protect_materializer_managed_files(
        self, tier: str
    ) -> None:
        body = Spawner._claude_code_default_settings(tier)
        deny = self._deny_list(body)
        assert deny == list(Spawner._PROTECTED_CLAUDE_DENY)
        assert "Edit(skills/**)" not in deny
        assert "Write(skills/**)" not in deny

    def test_restricted_strips_mutators(self) -> None:
        body = Spawner._claude_code_default_settings("restricted")
        allow = self._allow_list(body)
        assert "Bash" not in allow
        assert "Write" not in allow
        assert "Edit" not in allow
        assert "Task" not in allow
        assert "TodoWrite" not in allow
        # Read-side tools survive so the agent can still inspect.
        assert "Read" in allow
        assert "Glob" in allow
        assert "Grep" in allow
        # Web access is debatable; we keep it because "restricted"
        # in this PR is "no host mutation", not "no internet" —
        # network egress controls land in a separate issue.
        assert "WebSearch" in allow
        assert "WebFetch" in allow

    def test_trusted_matches_standard(self) -> None:
        # claude-code has no host-access dial beyond Bash, so trusted
        # ≡ standard. The tier label still propagates in case a
        # future claude-code SDK adds something we'd want to flip.
        assert (
            Spawner._claude_code_default_settings("trusted")
            == Spawner._claude_code_default_settings("standard")
        )

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="permission_level"):
            Spawner._claude_code_default_settings("godmode")
