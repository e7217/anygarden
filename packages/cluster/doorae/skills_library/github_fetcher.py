"""GitHub-backed fetcher for SkillLibrary (#119 Phase 1).

Calls the public GitHub REST endpoints directly over ``httpx``:

1. ``GET api.github.com/repos/<source>/git/trees/<rev>?recursive=1``
   resolves ``rev`` to a concrete commit SHA and lists every path
   in the repo at that commit.
2. ``GET raw.githubusercontent.com/<source>/<sha>/skills/<name>/SKILL.md``
   pulls the skill body at the pinned commit.

Picking this path over the ``skills`` Node CLI is deliberate — that
CLI would add a Node.js runtime to the cluster image and sends
telemetry about skill installs, and under the hood it performs the
same two requests this module issues.  See ``.tmp/plan-119`` §3.2
decision 1 for the full trade-off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx


class GitHubFetchError(RuntimeError):
    """Any non-retryable failure talking to GitHub."""


class SkillNotFoundError(GitHubFetchError):
    """The tree response didn't contain ``skills/<name>/SKILL.md``.

    Raised instead of a generic fetch error so the API layer can
    surface a specific 404 to the admin (wrong skill name / wrong
    repo layout) distinct from "GitHub itself is unhappy".
    """


class GitHubRateLimitError(GitHubFetchError):
    """Hit the anonymous rate limit (60/hour per IP).

    Phase 1 callers are admin-initiated so this is rare, but when it
    happens the caller wants to show "try again in an hour or set
    GITHUB_TOKEN" rather than a generic 5xx.
    """


@dataclass
class SkillFetchResult:
    """What ``GitHubFetcher.fetch_skill`` returns.

    ``commit_sha`` is the resolved SHA regardless of whether the
    caller asked for a branch, tag, or SHA — this is what gets
    pinned in the DB so later spawns don't depend on the network.
    """
    commit_sha: str
    skill_md: str
    # Non-SKILL.md paths *inside* ``skills/<name>/``. Phase 1 is
    # metadata-only — Phase 3 will promote these into a
    # ``{path: body}`` map and materialize them onto the agent disk.
    scripts_detected: list[str] = field(default_factory=list)


class GitHubFetcher:
    """Async GitHub tree + raw fetcher.

    Accepting an optional ``httpx.AsyncClient`` makes the class
    trivially testable: production wires ``None`` and the fetcher
    owns a short-lived client per request; tests pass a client
    backed by ``httpx.MockTransport``.
    """

    _TREE_URL = "https://api.github.com/repos/{source}/git/trees/{rev}"
    _RAW_URL = "https://raw.githubusercontent.com/{source}/{sha}/{path}"

    def __init__(
        self,
        client: Optional[httpx.AsyncClient] = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._external_client = client
        self._timeout = timeout

    async def fetch_skill(
        self,
        source: str,
        name: str,
        rev: str = "HEAD",
    ) -> SkillFetchResult:
        """Resolve ``rev`` then fetch ``skills/<name>/SKILL.md``.

        Raises:
          SkillNotFoundError: tree doesn't contain the expected path.
          GitHubRateLimitError: 403 with rate-limit marker.
          GitHubFetchError: any other non-2xx from either endpoint.
        """
        client = self._external_client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._external_client is None
        try:
            commit_sha, tree_entries = await self._resolve_tree(client, source, rev)
            skill_path = f"skills/{name}/SKILL.md"

            blob_paths = {
                entry["path"] for entry in tree_entries
                if entry.get("type") == "blob"
            }
            if skill_path not in blob_paths:
                raise SkillNotFoundError(
                    f"{skill_path} not found in {source}@{commit_sha}",
                )

            skill_md = await self._fetch_raw(client, source, commit_sha, skill_path)

            scripts = [
                p for p in blob_paths
                if p.startswith(f"skills/{name}/") and p != skill_path
            ]
            scripts.sort()

            return SkillFetchResult(
                commit_sha=commit_sha,
                skill_md=skill_md,
                scripts_detected=scripts,
            )
        finally:
            if owns_client:
                await client.aclose()

    # ── internal ──────────────────────────────────────────────────

    async def _resolve_tree(
        self,
        client: httpx.AsyncClient,
        source: str,
        rev: str,
    ) -> tuple[str, list[dict]]:
        url = self._TREE_URL.format(source=source, rev=rev)
        response = await client.get(url, params={"recursive": "1"})
        _raise_for_github_status(response)
        body = response.json()
        commit_sha = body.get("sha")
        entries = body.get("tree", [])
        if not commit_sha:
            raise GitHubFetchError(
                f"tree response for {source}@{rev} missing 'sha' field",
            )
        return commit_sha, entries

    async def _fetch_raw(
        self,
        client: httpx.AsyncClient,
        source: str,
        sha: str,
        path: str,
    ) -> str:
        url = self._RAW_URL.format(source=source, sha=sha, path=path)
        response = await client.get(url)
        if response.status_code != 200:
            raise GitHubFetchError(
                f"raw fetch {source}@{sha}/{path} → {response.status_code}",
            )
        return response.text


def _raise_for_github_status(response: httpx.Response) -> None:
    """Map GitHub error responses onto the error hierarchy above."""
    if response.is_success:
        return
    if response.status_code == 403:
        # Rate-limited responses expose ``X-RateLimit-Remaining: 0``;
        # generic 403s (e.g. private repo without auth) don't.
        if response.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubRateLimitError(
                "GitHub rate limit exceeded; set GITHUB_TOKEN or wait"
            )
    raise GitHubFetchError(
        f"GitHub returned {response.status_code}: {response.text[:200]}"
    )
