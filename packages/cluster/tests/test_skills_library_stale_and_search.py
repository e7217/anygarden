"""Tests for #126 — skills.sh search proxy + stale check + refresh.

Keeps every external call offline via ``httpx.MockTransport`` (same
pattern as ``test_skills_library_github.py``). No dependency on the
real skills.sh or GitHub — drift scenarios are driven by mutating the
fake fetcher between calls, search scenarios by swapping the
MockTransport handler.
"""

from __future__ import annotations

import asyncio
import os
import secrets

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, User
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.skills_library.github_fetcher import (
    GitHubFetcher,
    GitHubRateLimitError,
    SkillFetchResult,
)
from anygarden.skills_library.search import (
    SearchResult,
    SkillSearchError,
    search_skills,
)
from anygarden.skills_library.service import (
    SkillLibraryService,
    StaleCheckResult,
)


# ── GITHUB_TOKEN injection ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_github_fetcher_sends_token_header_when_set():
    """When ``GITHUB_TOKEN`` is wired into the fetcher, every request
    picks up the ``Authorization: Bearer …`` header. Tree + raw both
    need the header — raw.githubusercontent.com accepts the same
    token as the REST API."""
    observed_auths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_auths.append(request.headers.get("Authorization", ""))
        url = str(request.url)
        if "api.github.com" in url:
            return httpx.Response(
                200,
                json={
                    "sha": "deadbeef",
                    "tree": [
                        {
                            "path": "skills/x/SKILL.md",
                            "type": "blob",
                            "size": 10,
                            "sha": "x" * 40,
                            "mode": "100644",
                        },
                    ],
                    "truncated": False,
                },
            )
        return httpx.Response(200, text="# x")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    fetcher = GitHubFetcher(client=client, token="ghp_test")

    result = await fetcher.fetch_skill("owner/repo", "x", rev="HEAD")
    assert result.commit_sha == "deadbeef"
    # Both the tree request and the raw request must carry the header.
    assert all(a == "Bearer ghp_test" for a in observed_auths)
    assert len(observed_auths) == 2


@pytest.mark.asyncio
async def test_github_fetcher_no_auth_header_when_token_none():
    """Explicit ``token=None`` and no ``GITHUB_TOKEN`` env var → no
    Authorization header (anonymous, 60/h limit)."""
    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.headers.get("Authorization", ""))
        return httpx.Response(
            200,
            json={
                "sha": "s",
                "tree": [
                    {
                        "path": "skills/x/SKILL.md",
                        "type": "blob",
                        "size": 10,
                        "sha": "y" * 40,
                        "mode": "100644",
                    },
                ],
                "truncated": False,
            },
        )

    # Temporarily wipe the env var in case the dev environment has one set.
    prev = os.environ.pop("GITHUB_TOKEN", None)
    try:
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        fetcher = GitHubFetcher(client=client, token=None)
        # Force env to be blank for _this_ fetcher construction
        fetcher._token = None  # noqa: SLF001 — test hook
        await fetcher.resolve_head_sha("owner/repo", "HEAD")
        assert observed == [""]
    finally:
        if prev is not None:
            os.environ["GITHUB_TOKEN"] = prev


# ── resolve_head_sha ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_head_sha_does_not_fetch_any_raw_blobs():
    """The stale-check cron relies on ``resolve_head_sha`` being
    cheap — one tree request, zero raw blob fetches. This test fails
    the whole suite if the implementation regresses into a full
    ``fetch_skill`` by accident."""
    tree_calls = 0
    raw_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tree_calls, raw_calls
        url = str(request.url)
        if "api.github.com" in url:
            tree_calls += 1
            return httpx.Response(
                200,
                json={"sha": "aaaa", "tree": [], "truncated": False},
            )
        raw_calls += 1
        return httpx.Response(200, text="should never fire")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    fetcher = GitHubFetcher(client=client)

    sha = await fetcher.resolve_head_sha("owner/repo", "HEAD")
    assert sha == "aaaa"
    assert tree_calls == 1
    assert raw_calls == 0


# ── search.py ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_skills_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "skills.sh" in str(request.url)
        return httpx.Response(
            200,
            json={
                "skills": [
                    {
                        "id": "1",
                        "skillId": "web-design",
                        "name": "Web Design",
                        "installs": 42,
                        "source": "owner/repo",
                    },
                    {
                        "id": "2",
                        "skillId": "slack-bot",
                        "name": "Slack Bot",
                        "installs": 7,
                        "source": "other/slack",
                    },
                ],
                "count": 2,
                "duration_ms": 12,
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as c:
        results = await search_skills("design", limit=20, client=c)

    assert len(results) == 2
    assert results[0].name == "Web Design"
    assert results[0].source == "owner/repo"
    assert results[0].installs == 42


@pytest.mark.asyncio
async def test_search_skills_skips_malformed_rows():
    """One bad row shouldn't blank the whole list."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "skills": [
                    {"id": "1", "skillId": "ok", "name": "Good", "installs": 1, "source": "a/b"},
                    # missing ``source`` — should be dropped
                    {"id": "2", "skillId": "bad", "name": "Bad", "installs": 0},
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as c:
        results = await search_skills("", client=c)
    assert len(results) == 1
    assert results[0].skillId == "ok"


@pytest.mark.asyncio
async def test_search_skills_raises_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="maintenance")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(SkillSearchError):
            await search_skills("x", client=c)


# ── Stale check (service) ──────────────────────────────────────────


class _StaleFetcher:
    """Fake that returns a canned HEAD SHA plus a fetch_skill result.

    The attribute-based wiring (not a Protocol subclass) mirrors the
    fake in ``test_skills_library_api.py`` so the service can swap
    between registration and stale-probe paths without refactoring
    the tests.
    """

    def __init__(
        self,
        *,
        head_sha: str = "abcd",
        skill_md: str = "# body",
        commit_sha: str = "abcd",
    ):
        self.head_sha = head_sha
        self.skill_md = skill_md
        self.commit_sha = commit_sha
        self.raise_on_head: Exception | None = None

    async def fetch_skill(
        self, source: str, name: str, rev: str = "HEAD",
    ) -> SkillFetchResult:
        return SkillFetchResult(
            commit_sha=self.commit_sha,
            skill_md=self.skill_md,
            scripts_detected=[],
            extra_files={},
        )

    async def resolve_head_sha(
        self, source: str, rev: str = "HEAD",
    ) -> str:
        if self.raise_on_head is not None:
            raise self.raise_on_head
        return self.head_sha


@pytest_asyncio.fixture()
async def service_env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed an admin user — the audit row's actor_user_id FK requires a
    # real users.id. Using ``"admin-1"`` as a string id would trip
    # FOREIGN KEY constraint on SQLite in the service tests.
    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        db.add(admin)
        await db.flush()
        await db.commit()
        admin_id = admin.id

    fetcher = _StaleFetcher(head_sha="sha-v1", commit_sha="sha-v1")
    service = SkillLibraryService(factory, fetcher=fetcher)

    # Seed one registered (and approved) skill.
    result = await service.register(
        source="owner/repo", name="hello", actor_user_id=admin_id,
    )
    await service.approve(skill_id=result.entry.id, actor_user_id=admin_id)
    skill_id = result.entry.id

    try:
        yield {
            "factory": factory,
            "service": service,
            "fetcher": fetcher,
            "skill_id": skill_id,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_check_stale_flags_drift(service_env):
    service: SkillLibraryService = service_env["service"]
    factory = service_env["factory"]
    fetcher: _StaleFetcher = service_env["fetcher"]
    skill_id = service_env["skill_id"]

    # No drift yet — head matches pinned.
    async with factory() as db:
        result = await service.check_stale(db, skill_id)
    assert result is not None
    assert result.stale is False
    assert result.current_sha == "sha-v1"

    # Simulate upstream moving forward.
    fetcher.head_sha = "sha-v2"
    async with factory() as db:
        result = await service.check_stale(db, skill_id)
    assert result.stale is True
    assert result.current_sha == "sha-v2"
    assert result.pinned_rev == "sha-v1"


@pytest.mark.asyncio
async def test_check_stale_returns_none_for_missing_skill(service_env):
    service: SkillLibraryService = service_env["service"]
    factory = service_env["factory"]
    async with factory() as db:
        result = await service.check_stale(db, "does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_check_stale_absorbs_rate_limit(service_env):
    """A GitHub rate-limit error must not flip the skill into the
    'stale' state — the UI doesn't treat absence of data as drift."""
    service: SkillLibraryService = service_env["service"]
    factory = service_env["factory"]
    fetcher: _StaleFetcher = service_env["fetcher"]
    skill_id = service_env["skill_id"]

    fetcher.raise_on_head = GitHubRateLimitError("rate-limited")
    async with factory() as db:
        result = await service.check_stale(db, skill_id)
    assert result.stale is False
    assert result.error is not None
    assert "rate" in result.error.lower()


@pytest.mark.asyncio
async def test_check_all_stale_halts_on_rate_limit(service_env):
    """A rate-limit mid-sweep must break the loop — probing more
    skills against the same exhausted token window would just pile
    errors on one another."""
    service: SkillLibraryService = service_env["service"]
    fetcher: _StaleFetcher = service_env["fetcher"]

    # Register a second skill so the loop has >1 entry.
    fetcher.commit_sha = "sha-B"
    await service.register(source="owner/repo", name="second")

    call_count = 0

    async def head_impl(source: str, rev: str = "HEAD") -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise GitHubRateLimitError("rate limited immediately")
        return "sha-later"

    fetcher.resolve_head_sha = head_impl  # type: ignore[assignment]

    results = await service.check_all_stale()
    # First call raised → loop broke → second skill never probed.
    assert call_count == 1
    assert results == {}


# ── API layer (search + stale + refresh) ─────────────────────────────


class _FakeFetcherDriver:
    """Same fake as test_skills_library_api.py but also exposes
    ``resolve_head_sha`` so the stale-check / refresh API paths
    don't explode looking for an attribute."""

    def __init__(self, result: SkillFetchResult, head_sha: str = "sha-v1"):
        self.result = result
        self.head_sha = head_sha

    async def fetch_skill(
        self, source: str, name: str, rev: str = "HEAD",
    ) -> SkillFetchResult:
        return self.result

    async def resolve_head_sha(
        self, source: str, rev: str = "HEAD",
    ) -> str:
        return self.head_sha


@pytest_asyncio.fixture()
async def api_env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        agent = Agent(
            engine="echo", name="a1",
            desired_state="idle", actual_state="idle",
        )
        db.add_all([admin, agent])
        await db.flush()
        await db.commit()
        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret,
        )

    fetcher = _FakeFetcherDriver(
        SkillFetchResult(
            commit_sha="sha-v1",
            skill_md="# Hello\nv1",
            scripts_detected=[],
            extra_files={},
        ),
        head_sha="sha-v1",
    )
    service = SkillLibraryService(factory, fetcher=fetcher)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = lifecycle
    app.state.skill_library_service = service
    # Disable the stale cron during tests — we drive the cache
    # directly. ``skill_stale_task = sentinel`` prevents lifespan
    # from spawning its own.
    class _DoneTask:
        def done(self) -> bool: return True
    app.state.skill_stale_task = _DoneTask()  # type: ignore[assignment]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": admin_token,
            "app": app,
            "fetcher": fetcher,
            "factory": factory,
            "service": service,
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_endpoint_proxies_skills_sh(api_env, monkeypatch):
    client: AsyncClient = api_env["client"]
    token = api_env["token"]

    async def fake_search(query, *, limit=20, client=None, timeout=10.0):
        return [
            SearchResult(
                id="1", skillId="web-design", name="Web Design",
                installs=10, source="owner/repo",
            ),
        ]

    monkeypatch.setattr(
        "anygarden.api.v1.skills.skills_sh_search", fake_search,
    )

    resp = await client.get(
        "/api/v1/admin/skills/search?q=design&limit=5",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["skillId"] == "web-design"


@pytest.mark.asyncio
async def test_search_endpoint_caches_for_60s(api_env, monkeypatch):
    client: AsyncClient = api_env["client"]
    token = api_env["token"]
    call_count = 0

    async def fake_search(query, *, limit=20, client=None, timeout=10.0):
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(
        "anygarden.api.v1.skills.skills_sh_search", fake_search,
    )

    r1 = await client.get(
        "/api/v1/admin/skills/search?q=same",
        headers={"Authorization": f"Bearer {token}"},
    )
    r2 = await client.get(
        "/api/v1/admin/skills/search?q=same",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r1.status_code == 200 and r2.status_code == 200
    assert call_count == 1  # cache hit on second call


@pytest.mark.asyncio
async def test_search_endpoint_502_on_upstream_error(api_env, monkeypatch):
    client: AsyncClient = api_env["client"]
    token = api_env["token"]

    async def fake_search(query, *, limit=20, client=None, timeout=10.0):
        raise SkillSearchError("upstream down")

    monkeypatch.setattr(
        "anygarden.api.v1.skills.skills_sh_search", fake_search,
    )

    resp = await client.get(
        "/api/v1/admin/skills/search?q=down",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_stale_endpoint_returns_cache_contents(api_env):
    client: AsyncClient = api_env["client"]
    token = api_env["token"]
    app = api_env["app"]

    # Seed cache directly.
    app.state.skill_stale_cache = {
        "s1": StaleCheckResult(
            skill_id="s1", pinned_rev="old", current_sha="new",
            stale=True,
        ),
        "s2": StaleCheckResult(
            skill_id="s2", pinned_rev="same", current_sha="same",
            stale=False,
        ),
    }

    resp = await client.get(
        "/api/v1/admin/skills/stale",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    items = {row["skill_id"]: row for row in resp.json()}
    assert items["s1"]["stale"] is True
    assert items["s2"]["stale"] is False


@pytest.mark.asyncio
async def test_list_skills_merges_stale_flag(api_env):
    client: AsyncClient = api_env["client"]
    token = api_env["token"]
    app = api_env["app"]

    # Register a skill.
    reg = await client.post(
        "/api/v1/admin/skills",
        json={"source": "owner/repo", "name": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    skill_id = reg.json()["id"]

    # No stale marker yet → stale=False.
    resp = await client.get(
        "/api/v1/admin/skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()[0]["stale"] is False

    # Seed stale.
    app.state.skill_stale_cache[skill_id] = StaleCheckResult(
        skill_id=skill_id, pinned_rev="sha-v1",
        current_sha="sha-v2", stale=True,
    )
    resp = await client.get(
        "/api/v1/admin/skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()[0]["stale"] is True


@pytest.mark.asyncio
async def test_refresh_endpoint_creates_new_row_when_upstream_moved(api_env):
    """Phase 2 re-approval invariant: when upstream moves to a new SHA,
    refresh must mint a fresh pending row (approved_by=NULL) so the
    admin revalidates before the new content reaches agents."""
    client: AsyncClient = api_env["client"]
    token = api_env["token"]
    fetcher: _FakeFetcherDriver = api_env["fetcher"]
    app = api_env["app"]

    # Register + approve the original row at sha-v1.
    reg = await client.post(
        "/api/v1/admin/skills",
        json={"source": "owner/repo", "name": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    orig_skill_id = reg.json()["id"]
    await client.post(
        f"/api/v1/admin/skills/{orig_skill_id}/approve",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Seed a stale marker so we can verify refresh clears it.
    app.state.skill_stale_cache[orig_skill_id] = StaleCheckResult(
        skill_id=orig_skill_id, pinned_rev="sha-v1",
        current_sha="sha-v2", stale=True,
    )

    # Upstream moved — fetcher now returns a new SHA + new body.
    fetcher.result = SkillFetchResult(
        commit_sha="sha-v2",
        skill_md="# Hello v2",
        scripts_detected=[],
        extra_files={},
    )
    fetcher.head_sha = "sha-v2"

    resp = await client.post(
        f"/api/v1/admin/skills/{orig_skill_id}/refresh",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    new_row = resp.json()
    # Different row id — Phase 1 (source, name, pinned_rev) uniqueness.
    assert new_row["id"] != orig_skill_id
    assert new_row["pinned_rev"] == "sha-v2"
    # And the new row lands in PENDING per plan §3.2 B1 — admin must
    # re-approve before attach works.
    assert new_row["status"] == "pending"
    assert new_row["approved_by"] is None

    # Stale cache cleared for the old row.
    assert orig_skill_id not in app.state.skill_stale_cache

    # The old row is still visible (history preserved).
    list_resp = await client.get(
        "/api/v1/admin/skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    ids = {row["id"] for row in list_resp.json()}
    assert orig_skill_id in ids
    assert new_row["id"] in ids


@pytest.mark.asyncio
async def test_refresh_endpoint_same_sha_is_idempotent(api_env):
    """Same-SHA refresh must not explode and must not mint a new row."""
    client: AsyncClient = api_env["client"]
    token = api_env["token"]

    reg = await client.post(
        "/api/v1/admin/skills",
        json={"source": "owner/repo", "name": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    skill_id = reg.json()["id"]

    resp = await client.post(
        f"/api/v1/admin/skills/{skill_id}/refresh",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == skill_id


@pytest.mark.asyncio
async def test_refresh_endpoint_404_on_missing_skill(api_env):
    client: AsyncClient = api_env["client"]
    token = api_env["token"]

    resp = await client.post(
        "/api/v1/admin/skills/does-not-exist/refresh",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


# ── stale cron (lightweight sanity check) ────────────────────────


@pytest.mark.asyncio
async def test_stale_cron_populates_cache_and_cancels_cleanly(api_env):
    """Drive ``_run_skill_stale_cron`` directly with a tiny interval
    and verify it populates the cache at least once, then cancels
    without raising."""
    from anygarden.app import _run_skill_stale_cron

    app = api_env["app"]
    client: AsyncClient = api_env["client"]
    token = api_env["token"]
    fetcher: _FakeFetcherDriver = api_env["fetcher"]

    # Register a skill so the cron has something to probe.
    await client.post(
        "/api/v1/admin/skills",
        json={"source": "owner/repo", "name": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )

    # Simulate drift so the sweep records ``stale=True``.
    fetcher.head_sha = "sha-drifted"

    # Monkeypatch the warm-up sleep away by running the cron with a
    # tiny interval and cancelling after one sweep.
    task = asyncio.create_task(_run_skill_stale_cron(app, interval_seconds=0.01))
    # Let at least one sweep run.
    for _ in range(50):
        await asyncio.sleep(0.02)
        if app.state.skill_stale_cache:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    stale_entries = [
        r for r in app.state.skill_stale_cache.values() if r.stale
    ]
    assert len(stale_entries) >= 1
