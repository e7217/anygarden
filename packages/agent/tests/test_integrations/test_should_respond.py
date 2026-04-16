"""Tests for the should_respond unified gate."""

from unittest.mock import MagicMock

from doorae_agent.integrations.base import should_respond


def _make_client(
    agent_name: str = "테스트에이전트",
    my_pids: set | None = None,
    agent_id: str | None = None,
):
    client = MagicMock()
    client._agent_name = agent_name
    client._my_participant_ids = my_pids or {"my-pid-123"}
    client._agent_id = agent_id
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

    def test_mentioned_via_user_id_token(self):
        """Frontend autocomplete produces ``<@user:<participant_id>>``
        tokens; the server parses them as ``{type: user, id: …}``.
        The ``id`` is one of our ``_my_participant_ids`` — we must
        treat that as a direct mention and respond."""
        client = _make_client(my_pids={"alice-pid"})
        msg = {
            "participant_id": "human-pid",
            "content": "<@user:alice-pid> 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "user", "id": "alice-pid"}],
            },
        }
        assert should_respond(msg, client) is True

    def test_user_id_token_for_other_participant_skips(self):
        """Same autocomplete path but the ``id`` is someone else's
        participant_id (another agent, a human, or a guest). The
        current agent must stay out."""
        client = _make_client(my_pids={"alice-pid"})
        msg = {
            "participant_id": "human-pid",
            "content": "<@user:bob-pid> 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "user", "id": "bob-pid"}],
            },
        }
        assert should_respond(msg, client) is False

    def test_guest_user_id_mention_silences_all_agents(self):
        """Regression guard for the @guest fan-out bug: when a user
        mentions a guest via autocomplete, the server emits a
        ``{type: user, id: <guest_pid>}`` token. No agent's
        ``_my_participant_ids`` contains that id, so every agent
        should skip instead of treating the human sender as an
        implicit "respond" cue."""
        alice = _make_client(agent_name="Alice", my_pids={"alice-pid"})
        bob = _make_client(agent_name="Bob", my_pids={"bob-pid"})
        msg = {
            "participant_id": "human-pid",
            "content": "<@user:guest-pid> 안녕하세요",
            "metadata": {
                "mentions": [{"type": "user", "id": "guest-pid"}],
            },
        }
        assert should_respond(msg, alice) is False
        assert should_respond(msg, bob) is False

    def test_mentioned_in_metadata_legacy(self):
        """Server's ``parse_mentions`` emits legacy tokens as
        ``{"type": "legacy", "name": ...}``. A match on ``name``
        should trigger a response."""
        client = _make_client()
        msg = {
            "participant_id": "other",
            "content": "@테스트에이전트 안녕",
            "metadata": {
                "mentions": [{"type": "legacy", "name": "테스트에이전트"}],
                "_nonce": "x",
            },
        }
        assert should_respond(msg, client) is True

    def test_mentioned_in_content_with_space(self):
        """Names containing spaces aren't captured by the server's
        ``[\\w-]+`` pattern, so we fall back to a direct content scan."""
        client = _make_client(agent_name="테스트 에이전트")
        msg = {"participant_id": "other", "content": "@테스트 에이전트 안녕",
               "metadata": {"_nonce": "x"}}
        assert should_respond(msg, client) is True

    def test_human_message_without_mention_responds(self):
        """No addressable mentions from the server + human sender —
        keep the 1:1 / broadcast-to-room default behaviour."""
        client = _make_client()
        msg = {"participant_id": "human-pid", "content": "안녕하세요", "metadata": {}}
        assert should_respond(msg, client) is True

    def test_human_message_addressed_to_other_agent_skips(self):
        """Regression guard for the multi-agent fan-out bug: if the
        server parsed an explicit mention list and this agent isn't
        in it, stay out even when the sender is a human."""
        client = _make_client(agent_name="앨리스")
        msg = {
            "participant_id": "human-pid",
            "content": "@밥 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "legacy", "name": "밥"}],
            },
        }
        assert should_respond(msg, client) is False

    def test_human_message_addressed_to_me_among_others(self):
        """Multi-mention: as long as my name is in the list, I reply."""
        client = _make_client(agent_name="앨리스")
        msg = {
            "participant_id": "human-pid",
            "content": "@앨리스 @밥 회의 내용 정리",
            "metadata": {
                "mentions": [
                    {"type": "legacy", "name": "앨리스"},
                    {"type": "legacy", "name": "밥"},
                ],
            },
        }
        assert should_respond(msg, client) is True

    def test_room_only_mention_does_not_suppress(self):
        """``<#room:xyz>`` alone is a cross-room routing hint, not a
        user-addressed mention. It must not force this agent to skip
        — otherwise every ``#room`` query would silence the room."""
        client = _make_client()
        msg = {
            "participant_id": "human-pid",
            "content": "<#room:xyz> 의견 좀",
            "metadata": {"mentions": [{"type": "room", "id": "xyz"}]},
        }
        assert should_respond(msg, client) is True

    def test_agent_message_not_mentioned_skip(self):
        client = _make_client()
        msg = {"participant_id": "other-agent", "content": "네, 알겠습니다.",
               "metadata": {"_nonce": "some-nonce"}}
        assert should_respond(msg, client) is False

    def test_agent_message_mentioned_respond(self):
        client = _make_client()
        msg = {
            "participant_id": "other-agent",
            "content": "@테스트에이전트 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "legacy", "name": "테스트에이전트"}],
                "_nonce": "x",
            },
        }
        assert should_respond(msg, client) is True

    def test_content_scan_does_not_match_id_token(self):
        """Regression guard: an agent literally named ``user`` must
        not be falsely matched by the content substring ``@user``
        inside the ID-based token ``<@user:<pid>>``. Without the
        ``(?![\\w:])`` lookahead in the fallback content scan, the
        agent would wrongly claim the message is addressed to it
        and respond on every autocomplete mention."""
        client = _make_client(agent_name="user", my_pids={"unrelated-pid"})
        msg = {
            "participant_id": "human-pid",
            "content": "<@user:bob-pid> 확인해줘",
            "metadata": {"mentions": [{"type": "user", "id": "bob-pid"}]},
        }
        assert should_respond(msg, client) is False

    def test_content_scan_matches_name_with_space(self):
        """Counterpart to the previous test: the fallback content
        scan must still recognise names containing whitespace, which
        the server's ID/legacy regexes can't capture as a single
        token."""
        client = _make_client(agent_name="Alice Kim")
        msg = {
            "participant_id": "human-pid",
            "content": "@Alice Kim 봐줄래?",
            "metadata": {"mentions": [{"type": "legacy", "name": "Bob"}]},
        }
        assert should_respond(msg, client) is True

    def test_mention_name_is_case_insensitive(self):
        """Client-side casing of the agent name must not invert the
        routing: if the server captured ``@alice`` while the agent
        registered as ``Alice``, rule 3 should still match. Without
        casefolding the bug this PR fixes would flip polarity —
        the agent would be silenced instead of over-firing."""
        client = _make_client(agent_name="Alice")
        msg = {
            "participant_id": "human-pid",
            "content": "@alice 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "legacy", "name": "alice"}],
            },
        }
        assert should_respond(msg, client) is True

    def test_agent_message_addressed_to_other_agent_skips(self):
        """Agent-to-agent chatter targeted at someone else must not
        drag every other agent into the reply fan-out either."""
        client = _make_client(agent_name="앨리스")
        msg = {
            "participant_id": "other-agent",
            "content": "@밥 확인해줘",
            "metadata": {
                "mentions": [{"type": "legacy", "name": "밥"}],
                "_nonce": "x",
            },
        }
        assert should_respond(msg, client) is False

    def test_no_metadata(self):
        """Human message with no metadata at all."""
        client = _make_client()
        msg = {"participant_id": "human", "content": "hello"}
        assert should_respond(msg, client) is True

    def test_room_query_metadata_responds(self):
        """Legacy room_query metadata (no representative_agent_id) triggers
        response for backward compat — pre-#61 servers omit the field."""
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

    def test_room_query_representative_matches_responds(self):
        """Issue #61 — only the representative agent forwards.
        When ``representative_agent_id`` matches this client's
        ``_agent_id``, respond (representative does the forward)."""
        client = _make_client(agent_id="agent-rep-123")
        msg = {
            "participant_id": "human-pid",
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "room_query": {
                    "target_room_id": "xyz",
                    "source_room_id": "abc",
                    "representative_agent_id": "agent-rep-123",
                },
            },
        }
        assert should_respond(msg, client) is True

    def test_room_query_representative_mismatch_skips(self):
        """Issue #61 — non-representative agent must NOT forward.
        Without this gate N agents in the source room each send a
        duplicate [ROOM_QUERY] to the target room."""
        client = _make_client(agent_id="agent-other-456")
        msg = {
            "participant_id": "human-pid",
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "room_query": {
                    "target_room_id": "xyz",
                    "source_room_id": "abc",
                    "representative_agent_id": "agent-rep-123",
                },
            },
        }
        assert should_respond(msg, client) is False

    def test_room_query_legacy_client_no_agent_id_responds(self):
        """Pre-#61 clients (``_agent_id=None``) have no way to
        evaluate the gate — fall back to the legacy behaviour of
        always forwarding so the deploy transition doesn't drop
        queries entirely."""
        client = _make_client(agent_id=None)
        msg = {
            "participant_id": "human-pid",
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "room_query": {
                    "target_room_id": "xyz",
                    "source_room_id": "abc",
                    "representative_agent_id": "agent-rep-123",
                },
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
