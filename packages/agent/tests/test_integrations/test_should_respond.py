"""Tests for the should_respond unified gate."""

from unittest.mock import MagicMock

from doorae_agent.integrations.base import should_respond


def _make_client(agent_name: str = "테스트에이전트", my_pids: set | None = None):
    client = MagicMock()
    client._agent_name = agent_name
    client._my_participant_ids = my_pids or {"my-pid-123"}
    return client


class TestShouldRespond:
    def test_skip_own_message(self):
        client = _make_client()
        msg = {"participant_id": "my-pid-123", "content": "hello", "metadata": {}}
        assert should_respond(msg, client) is False

    def test_delegated_always_respond(self):
        client = _make_client()
        msg = {"participant_id": "other", "content": "[DELEGATED] do something", "metadata": {"_nonce": "x"}}
        assert should_respond(msg, client) is True

    def test_mentioned_in_metadata(self):
        client = _make_client()
        msg = {"participant_id": "other", "content": "@테스트에이전트 안녕",
               "metadata": {"mentions": ["테스트에이전트"], "_nonce": "x"}}
        assert should_respond(msg, client) is True

    def test_mentioned_in_content_with_space(self):
        client = _make_client(agent_name="테스트 에이전트")
        msg = {"participant_id": "other", "content": "@테스트 에이전트 안녕",
               "metadata": {"_nonce": "x"}}
        assert should_respond(msg, client) is True

    def test_human_message_always_respond(self):
        client = _make_client()
        msg = {"participant_id": "human-pid", "content": "안녕하세요", "metadata": {}}
        assert should_respond(msg, client) is True

    def test_agent_message_not_mentioned_skip(self):
        client = _make_client()
        msg = {"participant_id": "other-agent", "content": "네, 알겠습니다.",
               "metadata": {"_nonce": "some-nonce"}}
        assert should_respond(msg, client) is False

    def test_agent_message_mentioned_respond(self):
        client = _make_client()
        msg = {"participant_id": "other-agent", "content": "@테스트에이전트 이거 봐줘",
               "metadata": {"mentions": ["테스트에이전트"], "_nonce": "x"}}
        assert should_respond(msg, client) is True

    def test_no_metadata(self):
        """Human message with no metadata at all."""
        client = _make_client()
        msg = {"participant_id": "human", "content": "hello"}
        assert should_respond(msg, client) is True

    def test_room_query_metadata_responds(self):
        """room_query metadata triggers response even from agent sender."""
        client = _make_client()
        msg = {
            "participant_id": "other-agent",
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "_nonce": "x",
                "room_query": {"target_room_id": "xyz", "source_room_id": "abc"},
            },
        }
        assert should_respond(msg, client) is True

    def test_room_query_prefix_responds(self):
        """[ROOM_QUERY] prefix triggers response like [DELEGATED]."""
        client = _make_client()
        msg = {
            "participant_id": "other-agent",
            "content": "[ROOM_QUERY] 디자인룸에서 질문: API 설계 의견?",
            "metadata": {"_nonce": "x"},
        }
        assert should_respond(msg, client) is True
