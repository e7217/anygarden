"""Unit tests for ``expand_room_mentions`` (#739).

The server rewrites ``@everyone`` and the implicit call in a one-agent
room into ordinary ``user`` mentions so every later
routing step (agent rule 3/5, turn creation, #719, the peer safety net)
reuses the explicit-mention path.
"""

from __future__ import annotations

from anygarden.orchestration.rules import expand_room_mentions

AGENTS = ["pa", "pb", "pc"]


def _expand(mentions, **overrides):
    kwargs = {
        "agent_pids": AGENTS,
        "sender_pid": "human",
        "sender_is_human": True,
        "is_thread_reply": False,
    }
    kwargs.update(overrides)
    return expand_room_mentions(mentions, **kwargs)


def test_everyone_expands_to_every_agent():
    assert _expand([{"type": "everyone"}]) == [
        {"type": "user", "id": "pa", "via": "everyone"},
        {"type": "user", "id": "pb", "via": "everyone"},
        {"type": "user", "id": "pc", "via": "everyone"},
    ]


def test_everyone_excludes_the_sender():
    result = _expand(
        [{"type": "everyone"}], sender_pid="pb", sender_is_human=False
    )
    assert [m["id"] for m in result] == ["pa", "pc"]


def test_everyone_skips_already_mentioned_pids():
    result = _expand([{"type": "user", "id": "pb"}, {"type": "everyone"}])
    assert result == [
        {"type": "user", "id": "pb"},
        {"type": "user", "id": "pa", "via": "everyone"},
        {"type": "user", "id": "pc", "via": "everyone"},
    ]


def test_everyone_applies_in_threads():
    result = _expand([{"type": "everyone"}], is_thread_reply=True)
    assert [m["id"] for m in result] == AGENTS


def test_everyone_in_room_without_agents_disappears():
    assert _expand([{"type": "everyone"}], agent_pids=[]) == []


def test_sole_agent_added_for_unmentioned_human_root_message():
    assert _expand([], agent_pids=["pa"]) == [
        {"type": "user", "id": "pa", "via": "sole_agent"}
    ]


def test_sole_agent_keeps_room_mentions():
    room = {"type": "room", "id": "r1"}
    assert _expand([room], agent_pids=["pa"]) == [
        room,
        {"type": "user", "id": "pa", "via": "sole_agent"},
    ]


def test_sole_agent_not_added_when_someone_is_mentioned():
    user = [{"type": "user", "id": "human2"}]
    assert _expand(user, agent_pids=["pa"]) == user
    legacy = [{"type": "legacy", "name": "bob"}]
    assert _expand(legacy, agent_pids=["pa"]) == legacy


def test_sole_agent_requires_exactly_one_agent():
    assert _expand([]) == []
    assert _expand([], agent_pids=[]) == []


def test_sole_agent_requires_human_sender():
    assert _expand([], agent_pids=["pa"], sender_is_human=False, sender_pid="px") == []


def test_sole_agent_not_applied_to_thread_replies():
    assert _expand([], agent_pids=["pa"], is_thread_reply=True) == []


def test_input_is_not_mutated():
    mentions = [{"type": "everyone"}]
    _expand(mentions)
    assert mentions == [{"type": "everyone"}]


def test_names_agent_in_content():
    from anygarden.orchestration.rules import names_agent_in_content

    assert names_agent_in_content("agent", "@agent hello")
    assert names_agent_in_content("Alice", "hi @alice.")
    assert names_agent_in_content("테스트 에이전트", "@테스트 에이전트 안녕")
    assert not names_agent_in_content("agent", "@agents hi")
    assert not names_agent_in_content("user", "<@user:abc> hi")
    assert not names_agent_in_content("agent", "no mention")
    assert not names_agent_in_content(None, "@agent")
