"""Unit tests for search snippet rendering (#774)."""

from __future__ import annotations

from anygarden.api.v1.search import _HL_CLOSE, _HL_OPEN, _render_snippet


def _hl(text: str) -> str:
    return f"{_HL_OPEN}{text}{_HL_CLOSE}"


def test_user_markup_is_escaped_and_highlight_kept() -> None:
    raw = f"<script>alert(1)</script> {_hl('needle')}"
    assert _render_snippet(raw, {}, {}) == (
        "&lt;script&gt;alert(1)&lt;/script&gt; <mark>needle</mark>"
    )


def test_literal_mark_in_message_is_not_markup() -> None:
    assert _render_snippet("<mark>x</mark>", {}, {}) == "&lt;mark&gt;x&lt;/mark&gt;"


def test_mentions_resolve_to_names() -> None:
    raw = f"<@user:p1> see <#room:r1> {_hl('needle')}"
    assert _render_snippet(raw, {"p1": "pm"}, {"r1": "launch"}) == (
        "@pm see #launch <mark>needle</mark>"
    )


def test_unknown_mentions_and_names_are_escaped() -> None:
    assert _render_snippet("<@user:zz> <#room:yy>", {}, {}) == "@? #?"
    assert _render_snippet("<@user:p1>", {"p1": "<b>x</b>"}, {}) == "@&lt;b&gt;x&lt;/b&gt;"


def test_highlight_inside_token_still_resolves() -> None:
    raw = f"<@user:{_hl('p1')}>"
    assert _render_snippet(raw, {"p1": "pm"}, {}) == "@pm"
