"""Usage aggregation keeps historical values and its admin-only contract."""

from datetime import UTC, datetime, timedelta

import pytest
from anygarden.api.v1.usage import _parse_window
from anygarden.db.models import UsageLedger
from httpx import ASGITransport, AsyncClient

from .test_agents_api import agents_env as agents_env


@pytest.fixture
def env(agents_env):
    return dict(
        app=agents_env["app"],
        factory=agents_env["factory"],
        admin_jwt=agents_env["token"],
        user_jwt=agents_env["regular_token"],
    )


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_usage_aggregates_by_model_and_agent(env) -> None:
    from anygarden.db.models import Agent

    now = datetime.now(UTC)
    # Three requests: two for claude from agent-A, one for gpt from agent-B.
    # Create real Agent rows first so the FK on agent_id resolves.
    async with env["factory"]() as db:
        agent_a = Agent(name="A", engine="claude-code")
        agent_b = Agent(name="B", engine="codex")
        db.add_all([agent_a, agent_b])
        await db.flush()
        a_id, b_id = agent_a.id, agent_b.id

        rows = [
            UsageLedger(
                timestamp=now - timedelta(minutes=5),
                identity_kind="agent",
                identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=100,
                completion_tokens=50,
                duration_ms=800,
                status_code=200,
            ),
            UsageLedger(
                timestamp=now - timedelta(minutes=4),
                identity_kind="agent",
                identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=200,
                completion_tokens=80,
                duration_ms=900,
                status_code=200,
            ),
            UsageLedger(
                timestamp=now - timedelta(minutes=3),
                identity_kind="agent",
                identity_id=b_id,
                agent_id=b_id,
                model_name="gpt-5.4",
                prompt_tokens=75,
                completion_tokens=20,
                duration_ms=500,
                status_code=200,
            ),
            # 2-day-old row must be outside the default 24h window.
            UsageLedger(
                timestamp=now - timedelta(days=2),
                identity_kind="agent",
                identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=1,
                completion_tokens=1,
                duration_ms=10,
                status_code=200,
            ),
        ]
        db.add_all(rows)
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get("/api/v1/usage", headers=_auth(env["admin_jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 24
    assert body["total_requests"] == 3  # old row excluded

    by_model = {row["key"]: row for row in body["by_model"]}
    assert by_model["claude-sonnet-4-6"]["request_count"] == 2
    assert by_model["claude-sonnet-4-6"]["prompt_tokens"] == 300
    assert by_model["claude-sonnet-4-6"]["completion_tokens"] == 130
    assert by_model["gpt-5.4"]["request_count"] == 1

    by_agent = {row["key"]: row for row in body["by_agent"]}
    assert by_agent[a_id]["request_count"] == 2
    assert by_agent[b_id]["request_count"] == 1


async def test_usage_aggregates_cost_usd_nullable_safe(env) -> None:
    """#461 (Wave 2d) — the usage aggregation sums ``cost_usd`` per model /
    agent and a grand total, nullable-safe (rows with no cost contribute
    0). claude-code self-reports a cost; gateway-routed / codex rows leave
    it NULL."""
    from anygarden.db.models import Agent

    now = datetime.now(UTC)
    async with env["factory"]() as db:
        agent_a = Agent(name="A", engine="claude-code")
        agent_b = Agent(name="B", engine="codex")
        db.add_all([agent_a, agent_b])
        await db.flush()
        a_id, b_id = agent_a.id, agent_b.id

        rows = [
            # claude-code: self-reported costs.
            UsageLedger(
                timestamp=now - timedelta(minutes=5),
                identity_kind="agent",
                identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.01,
                duration_ms=800,
                status_code=200,
            ),
            UsageLedger(
                timestamp=now - timedelta(minutes=4),
                identity_kind="agent",
                identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=200,
                completion_tokens=80,
                cost_usd=0.02,
                duration_ms=900,
                status_code=200,
            ),
            # codex: tokens but NULL cost — must contribute 0 to the sums.
            UsageLedger(
                timestamp=now - timedelta(minutes=3),
                identity_kind="agent",
                identity_id=b_id,
                agent_id=b_id,
                model_name="gpt-5.4",
                prompt_tokens=75,
                completion_tokens=20,
                cost_usd=None,
                duration_ms=500,
                status_code=200,
            ),
        ]
        db.add_all(rows)
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get("/api/v1/usage", headers=_auth(env["admin_jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    # Grand total cost = 0.01 + 0.02 + 0 (codex NULL).
    assert body["total_cost_usd"] == pytest.approx(0.03)

    by_model = {row["key"]: row for row in body["by_model"]}
    assert by_model["claude-sonnet-4-6"]["cost_usd"] == pytest.approx(0.03)
    # NULL-cost codex row coalesces to 0, never errors.
    assert by_model["gpt-5.4"]["cost_usd"] == pytest.approx(0.0)

    by_agent = {row["key"]: row for row in body["by_agent"]}
    assert by_agent[a_id]["cost_usd"] == pytest.approx(0.03)
    assert by_agent[b_id]["cost_usd"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "window,hours",
    [
        ("1h", 1),
        ("24h", 24),
        ("7d", 168),
        ("30d", 720),
        ("0h", 1),
        ("99d", 720),
        ("bad", 24),
        ("", 24),
        (" 2H ", 2),
    ],
)
def test_usage_window_contract(window, hours):
    assert _parse_window(window) == hours


async def test_usage_admin_only_and_retired_routes_absent(env):
    from anygarden.db.models import Base
    from cryptography.fernet import Fernet
    from sqlalchemy import insert

    secret = "historical-provider-secret"
    ciphertext = Fernet(Fernet.generate_key()).encrypt(secret.encode())
    now = datetime.now(UTC)
    async with env["factory"]() as db:
        await db.execute(
            insert(Base.metadata.tables["llm_gateway_secrets"]).values(
                env_var_name="ARCHIVED_KEY",
                encrypted_value=ciphertext,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as client:
        assert (await client.get("/api/v1/usage")).status_code == 401
        assert (
            await client.get("/api/v1/usage", headers=_auth(env["user_jwt"]))
        ).status_code == 403
        empty = await client.get(
            "/api/v1/usage?window=7d", headers=_auth(env["admin_jwt"])
        )
        assert empty.status_code == 200
        assert secret not in empty.text and ciphertext.decode() not in empty.text
        assert "ARCHIVED_KEY" not in empty.text
        assert empty.json() == dict(
            window_hours=168,
            total_requests=0,
            total_cost_usd=0.0,
            by_model=[],
            by_agent=[],
        )
        for path in [
            "/api/v1/llm-gateway/models",
            "/api/v1/llm-gateway/secrets",
            "/api/v1/llm-gateway/status",
            "/api/v1/llm-gateway/usage",
            "/api/v1/llm/models",
        ]:
            assert (
                await client.get(path, headers=_auth(env["admin_jwt"]))
            ).status_code == 404
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert "gateway" not in health.json()["components"]
        assert "llm_gateway" not in str(env["app"].openapi())
