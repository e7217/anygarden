"""#854 — agent replies reference artifacts they uploaded.

The server rebuilds every ``artifact`` reference from ``room_artifacts`` so a
forged payload cannot surface another room's (or another agent's) file, and
drops reference types it does not know.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Project, Room, RoomArtifact
from anygarden.messages.references import (
    InvalidSharedFileReference,
    canonicalize_message_references,
)


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    session_factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as db:
        project = Project(name="p")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="general")
        other_room = Room(project_id=project.id, name="other")
        author = Agent(name="author", engine="codex")
        peer = Agent(name="peer", engine="codex")
        db.add_all([room, other_room, author, peer])
        await db.flush()
        artifact = RoomArtifact(
            room_id=room.id,
            produced_by_agent_id=author.id,
            filename="chart.png",
            storage_path=f"{room.id}/x",
            sha256="a" * 64,
            size_bytes=42,
            mime="image/png",
        )
        foreign = RoomArtifact(
            room_id=other_room.id,
            produced_by_agent_id=author.id,
            filename="secret.png",
            storage_path=f"{other_room.id}/y",
            sha256="b" * 64,
            size_bytes=7,
            mime="image/png",
        )
        db.add_all([artifact, foreign])
        await db.commit()
        yield {
            "db": db,
            "room_id": room.id,
            "author_id": author.id,
            "peer_id": peer.id,
            "artifact_id": artifact.id,
            "foreign_id": foreign.id,
        }
    await engine.dispose()


async def _canonicalize(env, refs, *, sender_agent_id, allow_room_artifacts=False):
    return await canonicalize_message_references(
        env["db"],
        room_id=env["room_id"],
        metadata={"references": refs},
        allow_shared_files=True,
        sender_agent_id=sender_agent_id,
        allow_room_artifacts=allow_room_artifacts,
    )


@pytest.mark.asyncio
async def test_agent_artifact_reference_is_rebuilt_from_db(env) -> None:
    out = await _canonicalize(
        env,
        [{"type": "artifact", "id": env["artifact_id"], "name": "spoof.exe",
          "mime": "text/html", "room_id": "forged"}],
        sender_agent_id=env["author_id"],
    )
    assert out["references"] == [
        {
            "type": "artifact",
            "id": env["artifact_id"],
            "room_id": env["room_id"],
            "name": "chart.png",
            "mime": "image/png",
            "size_bytes": 42,
            "sha256": "a" * 64,
        }
    ]


@pytest.mark.asyncio
async def test_duplicate_artifact_reference_is_collapsed(env) -> None:
    ref = {"type": "artifact", "id": env["artifact_id"]}
    out = await _canonicalize(env, [ref, ref], sender_agent_id=env["author_id"])
    assert len(out["references"]) == 1


@pytest.mark.asyncio
async def test_artifact_from_another_room_is_rejected(env) -> None:
    with pytest.raises(InvalidSharedFileReference):
        await _canonicalize(
            env,
            [{"type": "artifact", "id": env["foreign_id"]}],
            sender_agent_id=env["author_id"],
        )


@pytest.mark.asyncio
async def test_artifact_from_another_agent_is_rejected(env) -> None:
    with pytest.raises(InvalidSharedFileReference):
        await _canonicalize(
            env,
            [{"type": "artifact", "id": env["artifact_id"]}],
            sender_agent_id=env["peer_id"],
        )


@pytest.mark.asyncio
async def test_unknown_artifact_id_is_rejected(env) -> None:
    with pytest.raises(InvalidSharedFileReference):
        await _canonicalize(
            env,
            [{"type": "artifact", "id": "missing"}],
            sender_agent_id=env["author_id"],
        )


@pytest.mark.asyncio
async def test_human_artifact_reference_is_dropped(env) -> None:
    out = await _canonicalize(
        env,
        [{"type": "artifact", "id": env["artifact_id"]}],
        sender_agent_id=None,
    )
    assert "references" not in out


@pytest.mark.asyncio
async def test_unknown_reference_type_is_dropped(env) -> None:
    out = await _canonicalize(
        env,
        [{"type": "link", "href": "https://evil"},
         {"type": "artifact", "id": env["artifact_id"]}],
        sender_agent_id=env["author_id"],
    )
    assert [ref["type"] for ref in out["references"]] == ["artifact"]


# #873 — people attach images through room artifacts too. An artifact is
# already visible to every room participant, so a person may reference any
# artifact of the same room (including one an agent produced first: uploads
# dedupe on bytes). Agents keep the stricter own-artifacts-only rule.


@pytest.mark.asyncio
async def test_person_references_same_room_artifact(env) -> None:
    out = await _canonicalize(
        env,
        [{"type": "artifact", "id": env["artifact_id"], "name": "spoof.exe"}],
        sender_agent_id=None,
        allow_room_artifacts=True,
    )
    assert out["references"] == [
        {
            "type": "artifact",
            "id": env["artifact_id"],
            "room_id": env["room_id"],
            "name": "chart.png",
            "mime": "image/png",
            "size_bytes": 42,
            "sha256": "a" * 64,
        }
    ]


@pytest.mark.asyncio
async def test_person_cannot_reference_another_rooms_artifact(env) -> None:
    with pytest.raises(InvalidSharedFileReference):
        await _canonicalize(
            env,
            [{"type": "artifact", "id": env["foreign_id"]}],
            sender_agent_id=None,
            allow_room_artifacts=True,
        )


@pytest.mark.asyncio
async def test_agent_rule_ignores_room_artifact_flag(env) -> None:
    with pytest.raises(InvalidSharedFileReference):
        await _canonicalize(
            env,
            [{"type": "artifact", "id": env["artifact_id"]}],
            sender_agent_id=env["peer_id"],
            allow_room_artifacts=True,
        )
