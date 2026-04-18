"""Tests for /api/v1/admin/skills — skill_library (#119 Phase 1)."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, User
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus
from doorae.skills_library.github_fetcher import SkillFetchResult
from doorae.skills_library.service import SkillLibraryService


class FakeFetcher:
    """Canned SkillFetchResult so the API tests don't hit GitHub."""

    def __init__(self, result: SkillFetchResult) -> None:
        self.result = result

    async def fetch_skill(self, source: str, name: str, rev: str = "HEAD") -> SkillFetchResult:
        return self.result


@pytest_asyncio.fixture()
async def skills_env():
    config = DooraeSettings(
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
        regular = User(email="reg@test.com", password_hash="x", is_admin=False)
        agent = Agent(engine="echo", name="a1", desired_state="idle", actual_state="idle")
        db.add_all([admin, regular, agent])
        await db.flush()
        await db.commit()

        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret
        )
        regular_token = create_user_token(
            regular.id, regular.email, regular.is_admin, secret=config.jwt_secret
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.machine_bus = bus
        app.state.agent_lifecycle = lifecycle
        app.state.skill_library_service = SkillLibraryService(
            factory,
            fetcher=FakeFetcher(
                SkillFetchResult(
                    commit_sha="c0ffee",
                    skill_md="# Hello\nbody",
                    scripts_detected=["skills/hello/scripts/x.py"],
                )
            ),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "token": admin_token,
                "regular_token": regular_token,
                "factory": factory,
                "agent": agent,
            }

    await engine.dispose()


class TestSkillsAPI:
    @pytest.mark.asyncio
    async def test_register_creates_skill(self, skills_env):
        client, token = skills_env["client"], skills_env["token"]

        resp = await client.post(
            "/api/v1/admin/skills",
            json={"source": "owner/repo", "name": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["source"] == "owner/repo"
        assert body["name"] == "hello"
        assert body["pinned_rev"] == "c0ffee"
        assert body["scripts_detected"] == ["skills/hello/scripts/x.py"]
        assert "content_hash" in body

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self, skills_env):
        client, token = skills_env["client"], skills_env["regular_token"]

        resp = await client.post(
            "/api/v1/admin/skills",
            json={"source": "owner/repo", "name": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_returns_registered_skills(self, skills_env):
        client, token = skills_env["client"], skills_env["token"]
        # register one
        await client.post(
            "/api/v1/admin/skills",
            json={"source": "owner/repo", "name": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = await client.get(
            "/api/v1/admin/skills",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["name"] == "hello"

    @pytest.mark.asyncio
    async def test_delete_removes_skill_and_cascades_attachments(self, skills_env):
        client = skills_env["client"]
        token = skills_env["token"]
        agent = skills_env["agent"]

        reg = await client.post(
            "/api/v1/admin/skills",
            json={"source": "owner/repo", "name": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        skill_id = reg.json()["id"]

        await client.post(
            f"/api/v1/admin/skills/{skill_id}/attach",
            json={"agent_id": agent.id},
            headers={"Authorization": f"Bearer {token}"},
        )

        # Deletion cascades agent_skills via FK ondelete=CASCADE.
        resp = await client.delete(
            f"/api/v1/admin/skills/{skill_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Verify list is empty afterwards.
        resp = await client.get(
            "/api/v1/admin/skills",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_attach_and_detach_flow(self, skills_env):
        client = skills_env["client"]
        token = skills_env["token"]
        agent = skills_env["agent"]

        reg = await client.post(
            "/api/v1/admin/skills",
            json={"source": "owner/repo", "name": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        skill_id = reg.json()["id"]

        # Attach
        resp = await client.post(
            f"/api/v1/admin/skills/{skill_id}/attach",
            json={"agent_id": agent.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Double attach is idempotent.
        resp = await client.post(
            f"/api/v1/admin/skills/{skill_id}/attach",
            json={"agent_id": agent.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Listing surfaces attached agents count (shape: list of IDs).
        resp = await client.get(
            "/api/v1/admin/skills",
            headers={"Authorization": f"Bearer {token}"},
        )
        items = resp.json()
        assert items[0]["attached_agent_ids"] == [agent.id]

        # Detach
        resp = await client.delete(
            f"/api/v1/admin/skills/{skill_id}/attach/{agent.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        resp = await client.get(
            "/api/v1/admin/skills",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.json()[0]["attached_agent_ids"] == []

    @pytest.mark.asyncio
    async def test_attach_nonexistent_skill_returns_404(self, skills_env):
        client = skills_env["client"]
        token = skills_env["token"]
        agent = skills_env["agent"]

        resp = await client.post(
            "/api/v1/admin/skills/does-not-exist/attach",
            json={"agent_id": agent.id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_attach_nonexistent_agent_returns_404(self, skills_env):
        client = skills_env["client"]
        token = skills_env["token"]

        reg = await client.post(
            "/api/v1/admin/skills",
            json={"source": "owner/repo", "name": "hello"},
            headers={"Authorization": f"Bearer {token}"},
        )
        skill_id = reg.json()["id"]

        resp = await client.post(
            f"/api/v1/admin/skills/{skill_id}/attach",
            json={"agent_id": "missing-agent"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404
