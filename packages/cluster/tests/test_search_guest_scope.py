"""MSG-06 — ``GET /api/v1/search`` only returns messages from rooms the
caller can access. A guest is confined to the room its invite is bound to,
even when it also has a Participant row elsewhere.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_guest_token, create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_session_factory
from anygarden.db.models import (
    Message,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    User,
)

pytestmark = pytest.mark.req("MSG-06")

NEEDLE = "scopeneedle"


async def test_search_is_scoped_to_rooms_the_caller_can_access(
    engine, config: AnygardenSettings
) -> None:
    sessions = build_session_factory(engine)
    async with sessions() as db:
        member = User(email="member@anygarden.io", password_hash="x")
        outsider = User(email="outsider@anygarden.io", password_hash="x")
        guest = User(is_anonymous=True, display_name="G")
        project = Project(name="p")
        db.add_all([member, outsider, guest, project])
        await db.flush()
        guest_room = Room(project_id=project.id, name="guest-room")
        private_room = Room(project_id=project.id, name="private")
        db.add_all([guest_room, private_room])
        await db.flush()
        parts = {
            "member_g": Participant(room_id=guest_room.id, user_id=member.id, role="owner"),
            "member_p": Participant(room_id=private_room.id, user_id=member.id, role="owner"),
            "guest_g": Participant(room_id=guest_room.id, user_id=guest.id, role="member"),
            # A stray row in the private room must not widen the guest's scope.
            "guest_p": Participant(room_id=private_room.id, user_id=guest.id, role="member"),
        }
        db.add_all(parts.values())
        await db.flush()
        db.add_all(
            [
                Message(room_id=guest_room.id, participant_id=parts["member_g"].id,
                        content=f"{NEEDLE} in guest room", seq=1),
                Message(room_id=private_room.id, participant_id=parts["member_p"].id,
                        content=f"{NEEDLE} in private room", seq=1),
            ]
        )
        invite = RoomInviteLink(
            room_id=guest_room.id,
            created_by_user_id=member.id,
            token_hash="h",
            lookup_hint="inv_search00",
        )
        db.add(invite)
        await db.commit()
        ids = {
            "guest_room": guest_room.id,
            "private_room": private_room.id,
            "member": member.id,
            "outsider": outsider.id,
            "guest": guest.id,
            "invite": invite.id,
        }

    secret = config.jwt_secret
    tokens = {
        "member": create_user_token(ids["member"], "member@anygarden.io", False, secret=secret),
        "outsider": create_user_token(ids["outsider"], "outsider@anygarden.io", False, secret=secret),
        "guest": create_guest_token(
            user_id=ids["guest"],
            room_id=ids["guest_room"],
            invite_id=ids["invite"],
            display_name="G",
            secret=secret,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    }

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions

    async def rooms_found(who: str) -> set[str]:
        resp = await client.get(
            "/api/v1/search",
            params={"q": NEEDLE},
            headers={"Authorization": f"Bearer {tokens[who]}"},
        )
        assert resp.status_code == 200, resp.text
        return {hit["room_id"] for hit in resp.json()}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert await rooms_found("member") == {ids["guest_room"], ids["private_room"]}
        assert await rooms_found("guest") == {ids["guest_room"]}
        assert await rooms_found("outsider") == set()
