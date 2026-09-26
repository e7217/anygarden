"""Activity list compatibility, cursor boundaries and authorization."""

from datetime import UTC, datetime, timedelta

import pytest
from anygarden.db.models import ActivityLog, Agent

from .test_agents_api import agents_env as _agents_env

activity_env = _agents_env


async def seed(env):
    now = datetime(2026, 9, 26, 12, tzinfo=UTC)
    async with env["factory"]() as db:
        agent = Agent(
            name="history",
            engine="echo",
            desired_state="stopped",
            actual_state="stopped",
        )
        other = Agent(
            name="other-history",
            engine="echo",
            desired_state="stopped",
            actual_state="stopped",
        )
        db.add_all([agent, other])
        await db.flush()
        for suffix in "abcde":
            db.add(
                ActivityLog(
                    id=f"evt-{suffix}",
                    agent_id=agent.id,
                    timestamp=now,
                    event_type="handler_finished",
                    request_id=f"request-{suffix}",
                    outcome="failed" if suffix == "c" else "ok",
                    engine="codex-cli",
                )
            )
        db.add(
            ActivityLog(
                id="evt-f",
                agent_id=agent.id,
                timestamp=now + timedelta(microseconds=1),
                event_type="start_requested",
            )
        )
        db.add(
            ActivityLog(
                id="evt-0",
                agent_id=agent.id,
                timestamp=now - timedelta(seconds=1),
                event_type="stop_requested",
            )
        )
        db.add(
            ActivityLog(
                id="other-event",
                agent_id=other.id,
                timestamp=now,
                event_type="start_requested",
            )
        )
        await db.commit()
        return agent.id, now


def headers(env, regular=False):
    return {"Authorization": f"Bearer {env['regular_token' if regular else 'token']}"}


@pytest.mark.asyncio
async def test_older_pages_preserve_equal_timestamps_and_exclude_new_arrivals(
    activity_env,
):
    agent_id, now = await seed(activity_env)
    path = f"/api/v1/agents/{agent_id}/activity"
    params = {"limit": 2}
    seen = []
    for page in range(5):
        response = await activity_env["client"].get(
            path, params=params, headers=headers(activity_env)
        )
        assert response.status_code == 200
        rows = response.json()
        assert isinstance(rows, list)
        if not rows:
            break
        seen.extend(row["id"] for row in rows)
        last = rows[-1]
        params = {
            "limit": 2,
            "before_timestamp": last["timestamp"],
            "before_id": last["id"],
        }
        if page == 0:
            async with activity_env["factory"]() as db:
                db.add(
                    ActivityLog(
                        id="fresh-event",
                        agent_id=agent_id,
                        timestamp=now + timedelta(seconds=1),
                        event_type="start_requested",
                    )
                )
                await db.commit()
    assert seen == ["evt-f", "evt-e", "evt-d", "evt-c", "evt-b", "evt-a", "evt-0"]
    assert len(set(seen)) == len(seen)


@pytest.mark.asyncio
async def test_incremental_pages_are_ascending_with_same_time_replay_and_filters(
    activity_env,
):
    agent_id, now = await seed(activity_env)
    path = f"/api/v1/agents/{agent_id}/activity"
    params = {"limit": 2, "after_timestamp": now.isoformat(), "after_id": ""}
    seen = []
    for _ in range(5):
        response = await activity_env["client"].get(
            path, params=params, headers=headers(activity_env)
        )
        assert response.status_code == 200
        rows = response.json()
        if not rows:
            break
        seen.extend(row["id"] for row in rows)
        assert all(row["timestamp"].endswith("+00:00") for row in rows)
        params = {
            "limit": 2,
            "after_timestamp": rows[-1]["timestamp"],
            "after_id": rows[-1]["id"],
        }
    assert seen == ["evt-a", "evt-b", "evt-c", "evt-d", "evt-e", "evt-f"]
    filtered = await activity_env["client"].get(
        path,
        params={
            "outcome": "failed",
            "engine": "codex-cli",
            "before_timestamp": (now + timedelta(hours=9))
            .isoformat()
            .replace("+00:00", "+09:00"),
            "before_id": "evt-z",
        },
        headers=headers(activity_env),
    )
    assert [row["id"] for row in filtered.json()] == ["evt-c"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": 201},
        {"limit": "oops"},
        {"before_id": "evt-a"},
        {"before_timestamp": "2026-09-26T12:00:00Z"},
        {"after_id": ""},
        {"after_timestamp": "2026-09-26T12:00:00Z"},
        {"before_timestamp": "2026-09-26T12:00:00", "before_id": "evt-a"},
        {"before_timestamp": "bad-date", "before_id": "evt-a"},
        {
            "before_timestamp": "2026-09-26T12:00:00Z",
            "before_id": "evt-a",
            "after_timestamp": "2026-09-26T12:00:00Z",
            "after_id": "evt-b",
        },
    ],
)
async def test_invalid_activity_cursors_and_limits_are_rejected(activity_env, params):
    response = await activity_env["client"].get(
        "/api/v1/agents/test-agent/activity",
        params=params,
        headers=headers(activity_env),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_activity_access_remains_admin_only_and_default_is_a_list(activity_env):
    agent_id, _ = await seed(activity_env)
    path = f"/api/v1/agents/{agent_id}/activity"
    denied = await activity_env["client"].get(
        path, headers=headers(activity_env, regular=True)
    )
    assert denied.status_code == 403
    allowed = await activity_env["client"].get(path, headers=headers(activity_env))
    assert allowed.status_code == 200
    assert isinstance(allowed.json(), list)
    assert len(allowed.json()) == 7
