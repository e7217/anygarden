"""Unit tests for the gemini-cli permission-level → flag mapping (#309 PR-B).

Gemini's permission surface is binary at the CLI: ``--approval-mode
yolo`` (auto-approve tool calls) and ``--skip-trust`` (grant
workspace folder trust without prompt). The mapping table flips both
on for ``standard``/``trusted`` (= pre-#309 behaviour) and turns both
off for ``restricted`` so gemini falls back to the strict default
prompt-and-trust posture.
"""

from __future__ import annotations

import pytest

from anygarden_agent.integrations.gemini_cli import _resolve_gemini_flags


class TestResolveGeminiFlags:
    def test_none_falls_back_to_standard(self) -> None:
        flags = _resolve_gemini_flags(None)
        assert flags == {"approval_yolo": True, "skip_trust": True}

    def test_standard_matches_pre_309(self) -> None:
        flags = _resolve_gemini_flags("standard")
        assert flags == {"approval_yolo": True, "skip_trust": True}

    def test_restricted_disables_yolo_and_trust(self) -> None:
        flags = _resolve_gemini_flags("restricted")
        # No --approval-mode yolo → gemini falls back to its default
        # (interactive prompt) and refuses tool calls in non-interactive
        # mode rather than silently auto-approving.
        assert flags == {"approval_yolo": False, "skip_trust": False}

    def test_trusted_matches_standard_for_now(self) -> None:
        # Gemini has no host-access dial beyond what yolo grants, so
        # ``trusted`` is identical to ``standard`` at the CLI level.
        # The tier label still propagates so future gemini features
        # can plug in here without an API change.
        flags = _resolve_gemini_flags("trusted")
        assert flags == {"approval_yolo": True, "skip_trust": True}

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError, match="permission_level"):
            _resolve_gemini_flags("godmode")
