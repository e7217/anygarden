"""Tests for GitHubFetcher — skill_library (#119 Phase 1 / #123 Phase 3).

Uses ``httpx.MockTransport`` rather than respx so we don't drag a new
test-only dependency into the cluster package. ``GitHubFetcher``
accepts an optional AsyncClient, which is the injection point.
"""

from __future__ import annotations

import httpx
import pytest

from anygarden.skills_library.github_fetcher import (
    GitHubFetcher,
    SkillNotFoundError,
    GitHubRateLimitError,
    GitHubFetchError,
    SkillTooLargeError,
    UnsupportedSkillFileError,
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
async def test_resolves_skill_md_and_fetches_extra_files():
    """Phase 3: fetch SKILL.md AND every other blob inside the skill
    directory, returning bodies in ``extra_files``."""
    raw_bodies = {
        "skills/web-design/SKILL.md": "# Web Design\nbody here",
        "skills/web-design/scripts/fetch.py": "print('hi')",
        "skills/web-design/references/guide.md": "# Guide\ndetails",
    }

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
        prefix = "https://raw.githubusercontent.com/owner/repo/abc123abc123/"
        if url.startswith(prefix):
            path = url[len(prefix):]
            if path in raw_bodies:
                return httpx.Response(200, text=raw_bodies[path])
        raise AssertionError(f"unexpected request: {url}")

    fetcher = _make_fetcher(handler)
    result = await fetcher.fetch_skill("owner/repo", "web-design", rev="HEAD")

    assert result.commit_sha == "abc123abc123"
    assert result.skill_md == "# Web Design\nbody here"
    # ``extra_files`` contains the actual body of every non-SKILL.md
    # blob inside ``skills/<name>/``. Other skills in the repo stay out.
    assert result.extra_files == {
        "skills/web-design/scripts/fetch.py": "print('hi')",
        "skills/web-design/references/guide.md": "# Guide\ndetails",
    }
    # ``scripts_detected`` retains its list-of-paths shape (semantic
    # shifted from "detected only" to "actually fetched").
    assert set(result.scripts_detected) == set(result.extra_files.keys())


@pytest.mark.asyncio
async def test_rejects_per_file_over_1mb():
    """One file over 1 MB in the tree response must abort registration
    *before* any raw fetch — we rely on the ``size`` metadata to avoid
    pulling huge blobs onto the cluster."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(
                200,
                json=_tree_body([
                    _blob("skills/x/SKILL.md", size=100),
                    _blob("skills/x/huge.md", size=2 * 1024 * 1024),
                ]),
            )
        raise AssertionError(f"no raw fetch should happen: {url}")

    fetcher = _make_fetcher(handler)
    with pytest.raises(SkillTooLargeError) as ei:
        await fetcher.fetch_skill("owner/repo", "x", rev="HEAD")
    assert "huge.md" in str(ei.value)


@pytest.mark.asyncio
async def test_rejects_total_over_10mb():
    """Sum of per-file sizes over 10 MB must abort even when every file
    is individually under the per-file cap."""
    eight_hundred_kb = 800 * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(
                200,
                json=_tree_body([
                    _blob("skills/x/SKILL.md", size=eight_hundred_kb),
                    # 15 more files × 800 KB = 12 MB → over 10 MB total
                    *[
                        _blob(f"skills/x/part{i}.md", size=eight_hundred_kb)
                        for i in range(15)
                    ],
                ]),
            )
        raise AssertionError(f"no raw fetch should happen: {url}")

    fetcher = _make_fetcher(handler)
    with pytest.raises(SkillTooLargeError):
        await fetcher.fetch_skill("owner/repo", "x", rev="HEAD")


@pytest.mark.asyncio
async def test_rejects_unsupported_extension():
    """Any extra file whose extension isn't in the server-side
    whitelist aborts registration with a specific error so the admin
    knows to either drop the file or open a whitelist issue."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(
                200,
                json=_tree_body([
                    _blob("skills/x/SKILL.md"),
                    _blob("skills/x/data.parquet", size=1000),
                ]),
            )
        raise AssertionError(f"no raw fetch should happen: {url}")

    fetcher = _make_fetcher(handler)
    with pytest.raises(UnsupportedSkillFileError) as ei:
        await fetcher.fetch_skill("owner/repo", "x", rev="HEAD")
    assert ".parquet" in str(ei.value)
    assert "data.parquet" in str(ei.value)


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
