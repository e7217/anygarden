"""skills.sh search proxy (#126 Phase 5).

The admin UI delegates skill discovery to this module so the server
relays queries on the admin's behalf. Keeps admin identifiers off the
skills.sh side and centralises the response-shape coupling in one
place — if skills.sh changes its payload the adapter moves, not every
caller.

Picking an httpx.AsyncClient-injection entry-point mirrors
``GitHubFetcher`` so tests can wire a ``MockTransport`` without
monkey-patching module globals, and the API layer can share the same
client pooling decision (future optimisation).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import httpx


SKILLS_SH_SEARCH_URL = "https://skills.sh/api/search"


class SkillSearchError(RuntimeError):
    """skills.sh replied with a non-2xx or the payload was malformed.

    Separate exception type so the API layer can translate to a 502
    (upstream service problem, not an admin error) rather than a
    generic 500.
    """


@dataclass(frozen=True)
class SearchResult:
    """One row from skills.sh/api/search.

    Fields track the documented response schema. ``id`` is the
    skills.sh-internal db id; ``skillId`` is the stable identifier
    used across the skills.sh site; ``name`` is the display name;
    ``installs`` is an engagement counter (admins use it as a
    rough proxy for "is this well-known?"); ``source`` is the
    ``owner/repo`` string we can feed back into the register
    endpoint.
    """
    id: str
    skillId: str  # noqa: N815 — match upstream wire shape verbatim
    name: str
    installs: int
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


async def search_skills(
    query: str,
    *,
    limit: int = 20,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 10.0,
) -> list[SearchResult]:
    """Proxy a search query to skills.sh and parse the response.

    ``limit`` is passed straight through — skills.sh caps at its own
    maximum on the server side. An empty ``query`` still gets sent
    (skills.sh returns popular skills) because "show me popular
    skills" is a useful default for the admin UI's first paint.

    Raises ``SkillSearchError`` on non-2xx or payload parse errors;
    the API layer turns that into 502 with a short detail so the
    admin UI can render a "search unavailable" fallback.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)
    try:
        try:
            response = await client.get(
                SKILLS_SH_SEARCH_URL,
                params={"q": query, "limit": limit},
            )
        except httpx.HTTPError as exc:
            raise SkillSearchError(f"skills.sh request failed: {exc}") from exc
        if response.status_code >= 400:
            raise SkillSearchError(
                f"skills.sh returned {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise SkillSearchError(
                f"skills.sh returned non-JSON body: {response.text[:200]}"
            ) from exc

        rows = body.get("skills")
        if not isinstance(rows, list):
            raise SkillSearchError(
                "skills.sh payload missing 'skills' list — "
                f"keys={list(body.keys())}"
            )

        results: list[SearchResult] = []
        for row in rows:
            # Defensive parsing: skip any row missing the identifying
            # fields rather than fail the whole query. The admin UI
            # only needs "enough rows to choose from"; a malformed
            # upstream row shouldn't blank the whole list.
            try:
                results.append(
                    SearchResult(
                        id=str(row["id"]),
                        skillId=str(row["skillId"]),
                        name=str(row["name"]),
                        installs=int(row.get("installs", 0) or 0),
                        source=str(row["source"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return results
    finally:
        if owns_client:
            await client.aclose()
