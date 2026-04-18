"""GitHub-backed fetcher for SkillLibrary (#119 Phase 1 / #123 Phase 3).

Calls the public GitHub REST endpoints directly over ``httpx``:

1. ``GET api.github.com/repos/<source>/git/trees/<rev>?recursive=1``
   resolves ``rev`` to a concrete commit SHA and lists every path
   in the repo at that commit.
2. ``GET raw.githubusercontent.com/<source>/<sha>/skills/<name>/<path>``
   pulls each file body in ``skills/<name>/`` at the pinned commit —
   Phase 3 broadened this from "SKILL.md only" to the whole skill
   directory so helper scripts / references ride along with the
   skill onto the agent disk.

Picking this path over the ``skills`` Node CLI is deliberate — that
CLI would add a Node.js runtime to the cluster image and sends
telemetry about skill installs, and under the hood it performs the
same two requests this module issues.  See ``.tmp/plan-119`` §3.2
decision 1 for the full trade-off.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Optional

import httpx

from doorae.agent_files import _ALLOWED_EXTENSIONS


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


class SkillTooLargeError(GitHubFetchError):
    """A skill's files would exceed the per-file or total size budget.

    Raised *before* any blob is pulled over the network — we rely on
    the ``size`` metadata in the tree response so a malicious or
    inadvertently-huge skill can't burn cluster bandwidth or memory.
    """


class UnsupportedSkillFileError(GitHubFetchError):
    """A file in the skill directory has an extension outside the
    server-side whitelist (see ``doorae.agent_files._ALLOWED_EXTENSIONS``).

    Rejecting at registration time — instead of silently skipping —
    makes "why isn't my skill working?" debuggable. When a legitimate
    extension needs to be added, the fix is a whitelist update, not a
    code path that half-installs skills.
    """


# Per-file and per-skill size budgets.  Picked so that a normal skill
# (SKILL.md + a few small scripts/references) never trips the cap while
# still protecting the cluster from pathological inputs.
_PER_FILE_MAX = 1 * 1024 * 1024           # 1 MB
_TOTAL_SKILL_MAX = 10 * 1024 * 1024        # 10 MB


@dataclass
class SkillFetchResult:
    """What ``GitHubFetcher.fetch_skill`` returns.

    ``commit_sha`` is the resolved SHA regardless of whether the
    caller asked for a branch, tag, or SHA — this is what gets
    pinned in the DB so later spawns don't depend on the network.

    ``extra_files`` maps ``<rel_path_in_repo>`` → body for every blob
    in ``skills/<name>/`` other than SKILL.md.  Phase 1 left these as
    metadata (paths only); Phase 3 materializes the bodies onto the
    agent disk.

    ``scripts_detected`` is a sorted path list mirroring
    ``extra_files.keys()``.  Kept as a separate column for a JSON
    shape the admin UI can display without re-parsing the whole file
    map, and for backwards compat with Phase 1 response schemas.
    """
    commit_sha: str
    skill_md: str
    scripts_detected: list[str] = field(default_factory=list)
    extra_files: dict[str, str] = field(default_factory=dict)


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
        token: Optional[str] = None,
    ) -> None:
        self._external_client = client
        self._timeout = timeout
        # #126: GITHUB_TOKEN support lifts the anonymous 60/h limit to
        # the authenticated 5000/h limit, which the stale-check cron and
        # Phase 3's per-skill fan-out of raw blob fetches need. We read
        # the env var at construction time (not per-request) so a test
        # can construct a fetcher with ``token=None`` deterministically
        # even when the process env happens to have a token set.
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def _auth_headers(self) -> dict[str, str]:
        """Header dict that's empty when no token is configured.

        Keeping this inline-applied rather than setting client defaults
        so the injected-client test fixture (MockTransport) doesn't need
        to know about auth headers; the MockTransport handler can still
        assert on ``request.headers`` when a token test needs to.
        """
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def fetch_skill(
        self,
        source: str,
        name: str,
        rev: str = "HEAD",
    ) -> SkillFetchResult:
        """Resolve ``rev`` then fetch the whole ``skills/<name>/`` tree.

        Phase 3 behaviour: SKILL.md is mandatory; every other blob
        inside ``skills/<name>/`` is pulled in parallel and returned
        in ``extra_files``.  Extensions outside the server whitelist
        and files above the size budget abort the fetch.

        Raises:
          SkillNotFoundError: tree doesn't contain the expected path.
          SkillTooLargeError: per-file or total size budget exceeded.
          UnsupportedSkillFileError: whitelisted extension violation.
          GitHubRateLimitError: 403 with rate-limit marker.
          GitHubFetchError: any other non-2xx from either endpoint.
        """
        client = self._external_client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._external_client is None
        try:
            commit_sha, tree_entries = await self._resolve_tree(client, source, rev)
            skill_dir_prefix = f"skills/{name}/"
            skill_md_path = f"{skill_dir_prefix}SKILL.md"

            # Collect blob entries inside skills/<name>/ along with their
            # declared size (bytes). The tree API guarantees ``size`` on
            # blob rows — defensively default to 0 so a missing field
            # just falls through to the raw fetch rather than crashing.
            skill_blobs: list[tuple[str, int]] = []
            for entry in tree_entries:
                if entry.get("type") != "blob":
                    continue
                path = entry.get("path", "")
                if not path.startswith(skill_dir_prefix):
                    continue
                size = int(entry.get("size", 0) or 0)
                skill_blobs.append((path, size))

            blob_paths = {p for p, _ in skill_blobs}
            if skill_md_path not in blob_paths:
                raise SkillNotFoundError(
                    f"{skill_md_path} not found in {source}@{commit_sha}",
                )

            _validate_skill_contents(skill_blobs, name)

            # Parallel raw fetch: SKILL.md + every extra file. One
            # request per file is unavoidable with the raw endpoint
            # (there's no batch API), but asyncio.gather overlaps
            # network time to keep the registration latency bounded
            # by the slowest single request, not the sum.
            extra_paths = sorted(p for p in blob_paths if p != skill_md_path)
            ordered_paths = [skill_md_path, *extra_paths]
            bodies = await asyncio.gather(*[
                self._fetch_raw(client, source, commit_sha, p)
                for p in ordered_paths
            ])

            skill_md = bodies[0]
            extra_files = {
                p: body for p, body in zip(extra_paths, bodies[1:])
            }

            return SkillFetchResult(
                commit_sha=commit_sha,
                skill_md=skill_md,
                scripts_detected=extra_paths,
                extra_files=extra_files,
            )
        finally:
            if owns_client:
                await client.aclose()

    async def resolve_head_sha(
        self,
        source: str,
        rev: str = "HEAD",
    ) -> str:
        """Return the current commit SHA for ``source@rev`` without
        fetching any blob bodies.

        Used by the #126 stale-check cron: the expensive part of
        ``fetch_skill`` is the per-file raw fan-out, so a periodic
        "has upstream moved?" probe only needs the tree response's
        ``sha`` field. A drift here triggers a full ``fetch_skill``
        at refresh time so the canonical content hash can weigh in
        on whether the body actually changed (same SHA across
        branches, for instance, shouldn't mark every skill stale).
        """
        client = self._external_client or httpx.AsyncClient(timeout=self._timeout)
        owns_client = self._external_client is None
        try:
            commit_sha, _entries = await self._resolve_tree(client, source, rev)
            return commit_sha
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
        response = await client.get(
            url,
            params={"recursive": "1"},
            headers=self._auth_headers(),
        )
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
        # raw.githubusercontent.com accepts the same token as the REST
        # API; passing it lifts the per-IP anonymous limit on raw blob
        # fetches when GITHUB_TOKEN is set.
        response = await client.get(url, headers=self._auth_headers())
        if response.status_code != 200:
            raise GitHubFetchError(
                f"raw fetch {source}@{sha}/{path} → {response.status_code}",
            )
        return response.text


def _validate_skill_contents(
    skill_blobs: list[tuple[str, int]],
    name: str,
) -> None:
    """Enforce size caps + extension whitelist on the tree metadata.

    Pulled out of ``fetch_skill`` for readability and so the error
    exceptions carry enough context (path + budget) for admins to
    self-diagnose.  Called before any raw fetch happens so a bad
    skill manifest never hits the network a second time.
    """
    total = 0
    for path, size in skill_blobs:
        if size > _PER_FILE_MAX:
            raise SkillTooLargeError(
                f"{path} is {size} bytes, exceeds per-file limit "
                f"{_PER_FILE_MAX} bytes"
            )
        total += size
        # Whitelist check — PurePosixPath handles dotfiles and
        # multi-dot names the same way ``agent_files`` does.
        pure = PurePosixPath(path)
        file_name = pure.name
        if file_name.startswith(".") and "." not in file_name[1:]:
            suffix = file_name
        else:
            suffix = pure.suffix
        if suffix not in _ALLOWED_EXTENSIONS:
            raise UnsupportedSkillFileError(
                f"skill '{name}' contains file {path!r} with "
                f"extension {suffix!r} outside the allowed set; "
                "drop the file or extend the whitelist"
            )
    if total > _TOTAL_SKILL_MAX:
        raise SkillTooLargeError(
            f"skill '{name}' total size {total} bytes exceeds "
            f"{_TOTAL_SKILL_MAX} bytes"
        )


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
