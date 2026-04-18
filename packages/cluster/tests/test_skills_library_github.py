"""Tests for GitHubFetcher — skill_library (#119 Phase 1).

Uses ``httpx.MockTransport`` rather than respx so we don't drag a new
test-only dependency into the cluster package. ``GitHubFetcher``
accepts an optional AsyncClient, which is the injection point.
"""

from __future__ import annotations

import httpx
import pytest

from doorae.skills_library.github_fetcher import (
    GitHubFetcher,
    SkillNotFoundError,
    GitHubRateLimitError,
    GitHubFetchError,
)


def _tree_body(entries: list[dict], sha: str = "abc123abc123") -> dict:
    """Shape a GitHub git-trees recursive response."""
    return {
        "sha": sha,
        "url": "https://api.github.com/…",
        "tree": entries,
        "truncated": False,
    }


def _blob(path: str, size: int = 100) -> dict:
    return {"path": path, "type": "blob", "size": size, "sha": "x" * 40, "mode": "100644"}


def _make_fetcher(handler) -> GitHubFetcher:
    """Wire a MockTransport-backed AsyncClient into the fetcher."""
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return GitHubFetcher(client=client)


@pytest.mark.asyncio
async def test_resolves_skill_md_and_records_extra_paths():
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://api.github.com/repos/owner/repo/git/trees/HEAD"):
            return httpx.Response(
                200,
                json=_tree_body([
                    _blob("README.md"),
                    _blob("skills/web-design/SKILL.md", size=420),
                    _blob("skills/web-design/scripts/fetch.py", size=300),
                    _blob("skills/web-design/references/guide.md", size=800),
                    _blob("skills/other/SKILL.md"),
                ]),
            )
        if url == (
            "https://raw.githubusercontent.com/owner/repo/abc123abc123/"
            "skills/web-design/SKILL.md"
        ):
            return httpx.Response(200, text="# Web Design\nbody here")
        raise AssertionError(f"unexpected request: {url}")

    fetcher = _make_fetcher(handler)
    result = await fetcher.fetch_skill("owner/repo", "web-design", rev="HEAD")

    assert result.commit_sha == "abc123abc123"
    assert result.skill_md == "# Web Design\nbody here"
    # ``scripts_detected`` captures every non-SKILL.md blob *inside
    # the skill directory*. Other skills in the same repo are not
    # the caller's business in Phase 1.
    assert set(result.scripts_detected) == {
        "skills/web-design/scripts/fetch.py",
        "skills/web-design/references/guide.md",
    }


@pytest.mark.asyncio
async def test_raises_skill_not_found_when_tree_lacks_skill_md():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_tree_body([_blob("README.md")]),
        )

    fetcher = _make_fetcher(handler)
    with pytest.raises(SkillNotFoundError) as ei:
        await fetcher.fetch_skill("owner/empty", "missing", rev="main")
    assert "skills/missing/SKILL.md" in str(ei.value)


@pytest.mark.asyncio
async def test_raises_rate_limit_on_403_with_rate_limit_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )

    fetcher = _make_fetcher(handler)
    with pytest.raises(GitHubRateLimitError):
        await fetcher.fetch_skill("owner/repo", "foo", rev="HEAD")


@pytest.mark.asyncio
async def test_raises_fetch_error_on_404_repo():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    fetcher = _make_fetcher(handler)
    with pytest.raises(GitHubFetchError):
        await fetcher.fetch_skill("owner/missing", "foo", rev="HEAD")


@pytest.mark.asyncio
async def test_raises_fetch_error_when_skill_md_raw_is_missing():
    # Tree claims the file exists but raw 404s — shouldn't happen in
    # practice, but we want a clear error rather than an
    # AttributeError the day it does.
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(
                200,
                json=_tree_body([_blob("skills/x/SKILL.md")]),
            )
        return httpx.Response(404)

    fetcher = _make_fetcher(handler)
    with pytest.raises(GitHubFetchError):
        await fetcher.fetch_skill("owner/repo", "x", rev="HEAD")
