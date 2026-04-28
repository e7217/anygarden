"""Unit tests for the codex permission-level → native dial mapping (#309).

The mapping is a pure function so the matrix is exhaustive — four input
values (None, ``restricted``, ``standard``, ``trusted``) cross two
output dials (``sandbox``, ``approval_policy``) plus the rejection
path for anything else.
"""

from __future__ import annotations

import pytest

from doorae_agent.integrations.codex import _resolve_codex_flags


class TestResolveCodexFlags:
    def test_none_falls_back_to_standard(self) -> None:
        # The hot path post-#309: existing rows with NULL
        # ``permission_level`` keep the pre-#309 hardcoded behaviour.
        sandbox, approval = _resolve_codex_flags(None)
        assert sandbox == "workspace-write"
        assert approval == "never"

    def test_standard_matches_pre_309_defaults(self) -> None:
        sandbox, approval = _resolve_codex_flags("standard")
        assert sandbox == "workspace-write"
        assert approval == "never"

    def test_restricted_locks_to_read_only(self) -> None:
        sandbox, approval = _resolve_codex_flags("restricted")
        assert sandbox == "read-only"
        # ``untrusted`` keeps codex's default approval prompts so a
        # restricted agent can't silently elevate via tool calls — the
        # CLI either skips the call or surfaces it for review.
        assert approval == "untrusted"

    def test_trusted_unlocks_full_host_access(self) -> None:
        sandbox, approval = _resolve_codex_flags("trusted")
        assert sandbox == "danger-full-access"
        # ``never`` mirrors standard so the agent doesn't stall waiting
        # for an approval prompt when the operator has already granted
        # host access via the tier.
        assert approval == "never"

    def test_unknown_tier_raises(self) -> None:
        # Defensive — a typo (e.g. "trustred") shouldn't silently fall
        # through to an unintended dial. The API layer's pattern guard
        # prevents this from reaching production, but the helper is
        # the last line of defence for direct callers (cli, tests).
        with pytest.raises(ValueError, match="permission_level"):
            _resolve_codex_flags("godmode")
