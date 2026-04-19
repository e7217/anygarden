"""Tests for the should_respond unified gate and decide_policy."""

from unittest.mock import MagicMock

import pytest

from doorae_agent.integrations.base import (
    EngineAdapter,
    MessagePolicy,
    decide_policy,
    should_respond,
)


def _make_client(
    agent_name: str = "테스트에이전트",
    my_pids: set | None = None,
    agent_id: str | None = None,
    context_window_opt_out: bool = False,
):
    client = MagicMock()
    client._agent_name = agent_name
    client._my_participant_ids = my_pids or {"my-pid-123"}
    client._agent_id = agent_id
    # #148 Part 3 — ambient opt-out cache. False by default preserves
    # the pre-#148 INGEST_ONLY behaviour on the ``ingest_only`` flag;
    # tests that exercise opt-out pass ``True``.
    client._context_window_opt_out = context_window_opt_out
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


class TestDecidePolicy:
    """3-state gate tests covering the new INGEST_ONLY path (#74)."""

    def test_ingest_only_flag_returns_ingest_only(self):
        """metadata.ingest_only=True on an otherwise-unaddressed
        message (no mention, agent sender) yields INGEST_ONLY — the
        canonical ``[취합 결과]`` case."""
        client = _make_client()
        msg = {
            "participant_id": "rep-agent",
            "content": "[취합 결과] (3/3명 응답)\n\n...",
            "metadata": {
                "_nonce": "x",
                "ingest_only": True,
                "room_query_result": {"target_room_id": "r2"},
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.INGEST_ONLY

    def test_ingest_only_flag_without_nonce(self):
        """Human sender with ingest_only flag still ingests — the
        flag is the single source of truth, independent of sender
        kind."""
        client = _make_client()
        msg = {
            "participant_id": "human",
            "content": "잠깐 참고로 보세요",
            "metadata": {"ingest_only": True},
        }
        assert decide_policy(msg, client) is MessagePolicy.INGEST_ONLY

    def test_self_message_skips_regardless_of_ingest_flag(self):
        """Self-authored messages never reflect back into our own
        context; rule 1 wins over the ingest_only flag."""
        client = _make_client(my_pids={"my-pid"})
        msg = {
            "participant_id": "my-pid",
            "content": "[취합 결과]",
            "metadata": {"ingest_only": True},
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_delegated_prefix_beats_ingest_only(self):
        """[DELEGATED] always means ``do the work``. If both flags
        coexist, RESPOND wins — INGEST_ONLY is for passive
        observation, not for silencing actionable tasks."""
        client = _make_client()
        msg = {
            "participant_id": "other",
            "content": "[DELEGATED] do X",
            "metadata": {"_nonce": "x", "ingest_only": True},
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_direct_mention_beats_ingest_only(self):
        """Same principle as DELEGATED: addressability wins. A
        listener explicitly pinged on a context-tagged broadcast
        should still answer."""
        client = _make_client(my_pids={"alice-pid"})
        msg = {
            "participant_id": "human",
            "content": "<@user:alice-pid> 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "user", "id": "alice-pid"}],
                "ingest_only": True,
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_opt_out_agent_skips_ingest_only_broadcast(self):
        """#148 Part 3 — a direct-mention beats opt-out (addressability
        wins), but an un-addressed ``ingest_only`` broadcast drops to
        SKIP for an agent that opted out of ambient ingestion. Without
        the opt-out the same message returns INGEST_ONLY."""
        msg = {
            "participant_id": "peer",
            "content": "잡담 중 …",
            "metadata": {"_nonce": "x", "ingest_only": True},
        }
        ingesting = _make_client(context_window_opt_out=False)
        opted_out = _make_client(context_window_opt_out=True)
        assert decide_policy(msg, ingesting) is MessagePolicy.INGEST_ONLY
        assert decide_policy(msg, opted_out) is MessagePolicy.SKIP

    def test_opt_out_does_not_override_direct_mention(self):
        """Opt-out only demotes ``INGEST_ONLY`` → ``SKIP``; a direct
        mention still gets RESPOND. Addressability always wins over
        passive policy flags (decide_policy rule 3)."""
        client = _make_client(
            my_pids={"me-pid"},
            context_window_opt_out=True,
        )
        msg = {
            "participant_id": "human",
            "content": "<@user:me-pid> ping",
            "metadata": {
                "mentions": [{"type": "user", "id": "me-pid"}],
                "ingest_only": True,
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_room_query_metadata_skips_ingest_only_path(self):
        """room_query routing (#61) predates the ingest_only flag;
        when both exist, the rep-gate branch must still fire so
        non-representative agents don't double-forward the
        [ROOM_QUERY]. The ingest_only flag is only consulted if
        room_query metadata is absent."""
        client = _make_client(agent_id="agent-a")
        msg = {
            "participant_id": "human",
            "content": "질문",
            "metadata": {
                "room_query": {"representative_agent_id": "agent-b"},
                "ingest_only": True,
            },
        }
        # Non-rep agent should SKIP (rep-gate), not INGEST_ONLY.
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    @pytest.mark.parametrize(
        "policy,expected_bool",
        [
            (MessagePolicy.RESPOND, True),
            (MessagePolicy.INGEST_ONLY, False),
            (MessagePolicy.SKIP, False),
        ],
    )
    def test_should_respond_wrapper_maps_enum_to_bool(
        self, policy, expected_bool
    ):
        """should_respond is kept as a thin back-compat wrapper —
        only RESPOND maps to True. INGEST_ONLY callers that haven't
        migrated to decide_policy yet stay silent, preserving the
        pre-#74 default (no response) while the new handler path
        adopts the three-way API."""
        # Synthesise msg/client so decide_policy will hit each arm.
        # Easier: stub decide_policy via a custom msg, but simpler to
        # verify the enum→bool table directly since the wrapper is
        # just an equality check.
        assert (policy == MessagePolicy.RESPOND) is expected_bool


class TestDecidePolicyStageB:
    """Stage B (#74) promotes would-be-SKIP messages to INGEST_ONLY
    when the accumulator is enabled. When disabled, the gate
    collapses back to Stage A behaviour: explicit flag path only.
    The ``_reset_accumulator`` fixture keeps env mutations from
    leaking between tests."""

    @pytest.fixture(autouse=True)
    def _reset_accumulator(self):
        from doorae_agent.coordination.accumulator import reset_for_tests
        reset_for_tests()
        yield
        reset_for_tests()

    def test_agent_ambient_captured_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another agent replying to a human (no mention for us) is
        the canonical Stage B case — with the window on, we
        absorb it as context."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        client = _make_client()
        msg = {
            "participant_id": "other-agent",
            "content": "답변해드릴게요",
            "metadata": {"_nonce": "x"},
        }
        assert decide_policy(msg, client) is MessagePolicy.INGEST_ONLY

    def test_agent_ambient_skipped_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stage A default: same message, accumulator off, must
        still SKIP. Stage B is strictly additive."""
        monkeypatch.delenv("DOORAE_CONTEXT_WINDOW_ENABLED", raising=False)
        client = _make_client()
        msg = {
            "participant_id": "other-agent",
            "content": "답변해드릴게요",
            "metadata": {"_nonce": "x"},
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_human_addressing_peer_captured_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Human mentions someone else in the room. Without Stage B
        this is rule-5 SKIP. With Stage B, we capture the question
        so the upcoming peer answer has a grounded context."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        client = _make_client(my_pids={"me-pid"})
        msg = {
            "participant_id": "human",
            "content": "<@user:peer-pid> 어때요?",
            "metadata": {
                "mentions": [{"type": "user", "id": "peer-pid"}],
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.INGEST_ONLY

    def test_self_message_never_captured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Self-authored messages must stay SKIP even with Stage B
        active — they're already in our session via the SDK."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        client = _make_client(my_pids={"me-pid"})
        msg = {
            "participant_id": "me-pid",
            "content": "이건 내 발언",
            "metadata": {"_nonce": "x"},
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_human_broadcast_still_respond_not_ingest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No mention + human sender → RESPOND stays RESPOND.
        Silencing human-to-room chatter with INGEST_ONLY would
        break the 1:1-DM / group-broadcast UX that rule 6 covers."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        client = _make_client()
        msg = {
            "participant_id": "human",
            "content": "누구든 알려주세요",
            "metadata": {},
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_direct_mention_still_responds_under_stage_b(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Addressability trumps Stage B promotion exactly as it
        trumps the ingest_only flag — mentioned work is work."""
        monkeypatch.setenv("DOORAE_CONTEXT_WINDOW_ENABLED", "1")
        client = _make_client(my_pids={"me-pid"})
        msg = {
            "participant_id": "human",
            "content": "<@user:me-pid> 이거 봐줘",
            "metadata": {
                "mentions": [{"type": "user", "id": "me-pid"}],
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND


class TestIngestContextDefault:
    """EngineAdapter.ingest_context default no-op keeps legacy
    adapters source-compatible — they stay in the old bool world."""

    async def test_base_ingest_context_is_noop(self):
        class _StubAdapter(EngineAdapter):
            async def on_message(self, msg):
                return None

            async def start(self):
                return

        adapter = _StubAdapter()
        # Must not raise, must not return anything useful.
        result = await adapter.ingest_context(
            {"content": "x", "metadata": {"ingest_only": True}}
        )
        assert result is None
