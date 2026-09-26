"""Public role descriptions reach room members without admin-agent access."""

import pytest
from httpx import ASGITransport, AsyncClient

from anygarden.auth.jwt import create_user_token
from anygarden.db.models import Agent, Participant, User
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.ws.manager import ConnectionManager

from .test_guest_filtering import env as room_env

env = room_env


@pytest.mark.parametrize("viewer", ["owner_token", "guest_jwt"])
async def test_room_members_read_role_and_receive_updated_description(env, config, viewer):
    app = env["app"]
    app.state.machine_bus = MachineBus()
    app.state.agent_lifecycle = AgentLifecycle(
        db_factory=env["session_factory"], machine_bus=app.state.machine_bus,
    )
    app.state.connection_manager = ConnectionManager()
    frames = []

    async def capture(room_id, frame, exclude_participant_id=None):
        frames.append(frame)

    app.state.connection_manager.broadcast = capture
    async with env["session_factory"]() as db:
        admin = User(email="profile-admin@example.test", password_hash="x", is_admin=True)
        agent = Agent(name="Reviewer", engine="codex-cli", description="Reviews changes",
                      avatar_kind="emoji", avatar_value="🔎")
        db.add_all([admin, agent])
        await db.flush()
        part = Participant(room_id=env["room"].id, agent_id=agent.id, role="member")
        db.add(part)
        await db.commit()
        agent_id, participant_id = agent.id, part.id
        admin_token = create_user_token(admin.id, admin.email, True, secret=config.jwt_secret)

    auth = {"Authorization": f"Bearer {env[viewer]}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/agents", headers=auth)).status_code == 403
        response = await client.get(f"/api/v1/rooms/{env['room'].id}", headers=auth)
        assert response.status_code == 200
        participants = response.json()["participants"]
        actual = next(p for p in participants if p["id"] == participant_id)
        assert actual["description"] == "Reviews changes"
        assert actual["engine"] == "codex-cli"
        assert actual["avatar_value"] == "🔎"
        assert all(p["description"] is None for p in participants if p["kind"] == "user")

        for description in ("Maintains the release", None):
            saved = await client.put(f"/api/v1/agents/{agent_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"description": description, "description_set": True})
            assert saved.status_code == 200, saved.text
            roster_frames = [f for f in frames if getattr(f, "participants", None) is not None]
            assert next(p for p in roster_frames[-1].participants if p.id == participant_id).description == description
            latest = await client.get(f"/api/v1/rooms/{env['room'].id}", headers=auth)
            assert next(p for p in latest.json()["participants"] if p["id"] == participant_id)["description"] == description

        other = await client.get(f"/api/v1/rooms/{env['other_room'].id}", headers=auth)
        assert other.status_code == 403
