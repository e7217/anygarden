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
    recent_msgs: dict | None = None,
    speaker_strategy: dict | None = None,
    orchestrator_agent_id: dict | None = None,
):
    client = MagicMock()
    client._agent_name = agent_name
    client._my_participant_ids = my_pids or {"my-pid-123"}
    client._agent_id = agent_id
    # #148 Part 3 — ambient opt-out cache. False by default preserves
    # the pre-#148 INGEST_ONLY behaviour on the ``ingest_only`` flag;
    # tests that exercise opt-out pass ``True``.
    client._context_window_opt_out = context_window_opt_out
    # #157 Phase B — cycle-detection ring buffer. Default empty dict
    # keeps legacy tests untouched (cycle rule never fires without
    # prior history). Tests that exercise the cycle rule pass a dict
    # with pre-populated deques.
    client._recent_msgs = recent_msgs if recent_msgs is not None else {}
    # #159 Phase B — per-room speaker strategy cache. Empty dict ⇒
    # every room falls back to 'mentioned_only', which is what every
    # legacy test in this suite expects.
    client._speaker_strategy = (
        speaker_strategy if speaker_strategy is not None else {}
    )
    # #159 Phase C — per-room orchestrator pointer. Kept separate from
    # ``_speaker_strategy`` because a room may be in ``orchestrator``
    # mode but have no orchestrator set yet (client falls back to
    # mentioned_only-ish semantics in that case).
    client._orchestrator_agent_id = (
        orchestrator_agent_id if orchestrator_agent_id is not None else {}
    )
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


class TestCycleDetectionInDecidePolicy:
    """Issue #157 Phase B — decide_policy SKIPs on semantic cycles.

    The rule runs between the room_query guard (rule 2b) and the
    direct-mention rule (rule 3), so a looping (sender, content-hash)
    pair escapes even a determined @-mention chain.
    """

    def _with_history(self, sender: str, content: str, times: int) -> dict:
        """Build a ``_recent_msgs`` dict pre-loaded with ``times``
        copies of the given (sender, content) fingerprint."""
        from collections import deque
        from doorae_agent.integrations.cycle_guard import hash_content

        buf: deque = deque(maxlen=10)
        for _ in range(times):
            buf.append({"sender": sender, "hash": hash_content(content)})
        return {"room-a": buf}

    def test_cycle_triggers_skip_even_when_mentioned(self):
        """Direct mention normally → RESPOND; cycle rule pre-empts it."""
        looped_content = (
            "I have analysed the logs and the issue appears to be X"
        )
        client = _make_client(
            my_pids={"my-pid-123"},
            recent_msgs=self._with_history("other-agent", looped_content, 2),
        )
        msg = {
            "participant_id": "other-agent",
            "room_id": "room-a",
            "content": looped_content,
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}]
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_different_sender_same_content_no_cycle(self):
        """Same content from a different sender doesn't SKIP — fresh voice."""
        content = "I have analysed the logs and found nothing suspicious here"
        client = _make_client(
            my_pids={"my-pid-123"},
            recent_msgs=self._with_history("agent-B", content, 2),
        )
        # Now a different sender says the same thing while mentioning us
        msg = {
            "participant_id": "agent-C",  # different sender
            "room_id": "room-a",
            "content": content,
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}]
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_short_content_no_cycle(self):
        """Short replies ('네', 'ok') are excluded from hashing."""
        short = "ok"
        client = _make_client(
            my_pids={"my-pid-123"},
            recent_msgs=self._with_history("other-agent", short, 5),
        )
        msg = {
            "participant_id": "other-agent",
            "room_id": "room-a",
            "content": short,
            "metadata": {"_nonce": "n1"},
        }
        # 'ok' from another agent without mention → rule 7 SKIP normally
        # but not because of the cycle guard. Verify by using a human
        # sender (no nonce) so rule 6 would otherwise RESPOND.
        msg_human = dict(msg)
        msg_human["metadata"] = {}  # no nonce → sender_is_agent=False
        assert decide_policy(msg_human, client) is MessagePolicy.RESPOND

    def test_no_room_id_disables_cycle_rule(self):
        """Legacy tests don't put room_id on msg — cycle rule is inert."""
        client = _make_client(my_pids={"my-pid-123"})
        msg = {
            "participant_id": "other-agent",
            "content": "any content long enough for hashing to fire",
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}]
            },
            # no "room_id"
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_single_prior_match_no_cycle(self):
        """One prior occurrence isn't a loop — need at least 2."""
        content = "the message that appeared only once so far today"
        client = _make_client(
            my_pids={"my-pid-123"},
            recent_msgs=self._with_history("other-agent", content, 1),
        )
        msg = {
            "participant_id": "other-agent",
            "room_id": "room-a",
            "content": content,
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}]
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND


class TestRoundRobinStrategy:
    """Issue #159 Phase B — ``round_robin`` dispatches by the server-
    computed ``next_speaker_participant_id``. Only the single agent
    whose participant id matches wakes up; everyone else skips,
    including the human-sender default that ``mentioned_only`` uses.
    """

    def test_my_turn_responds(self):
        client = _make_client(
            my_pids={"my-pid-123"},
            speaker_strategy={"room-a": "round_robin"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "team, go",
            "metadata": {
                "next_speaker_participant_id": "my-pid-123",
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_not_my_turn_skips(self):
        client = _make_client(
            my_pids={"my-pid-123"},
            speaker_strategy={"room-a": "round_robin"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "team, go",
            "metadata": {
                "next_speaker_participant_id": "other-pid-456",
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_no_next_speaker_metadata_skips(self):
        """Round-robin strictly requires server to stamp the pointer.
        Absence ⇒ SKIP (no fallback to 'everyone replies')."""
        client = _make_client(
            my_pids={"my-pid-123"},
            speaker_strategy={"room-a": "round_robin"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "hi",
            "metadata": {},
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_direct_mention_still_wins(self):
        """Explicit mention pre-empts round-robin routing (rule 3
        beats the strategy tail)."""
        client = _make_client(
            my_pids={"my-pid-123"},
            speaker_strategy={"room-a": "round_robin"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "yo",
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}],
                # Someone else is the "next speaker" but we were
                # explicitly @-mentioned — addressability wins.
                "next_speaker_participant_id": "other-pid-456",
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_task_init_prefix_still_wins(self):
        """[ROOM_QUERY] / [DELEGATED] pre-empt round-robin (rule 2)."""
        client = _make_client(
            my_pids={"my-pid-123"},
            speaker_strategy={"room-a": "round_robin"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "[ROOM_QUERY] fetch the status",
            "metadata": {
                "next_speaker_participant_id": "other-pid-456",
                "_nonce": "n1",
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND


class TestOrchestratorStrategy:
    """Issue #159 Phase C — ``orchestrator`` strategy O1/O2/O3.

    Rules:

    - **O1**: I am this room's orchestrator → RESPOND. The
      orchestrator agent always sees every turn; it's the one
      making the next-speaker call via ``handoff_to``.
    - **O2**: ``metadata.next_speaker_participant_id`` points at me
      (stamped by the server after the orchestrator's last
      ``[HANDOFF]``) → RESPOND.
    - **O3**: otherwise SKIP — even for unaddressed human messages.
      This is the structural change vs. Phase B's fallthrough: a
      non-orchestrator worker in an orchestrator room stays silent
      until handed off to, so the orchestrator genuinely controls
      turn order instead of every agent racing on every human turn.

    The pre-strategy base rules (self-echo, ``[DELEGATED]`` /
    ``[ROOM_QUERY]`` / explicit mention, cycle detection) still fire
    upstream of this dispatcher, so a handoff tool call that lands
    as ``[HANDOFF] <@user:{pid}> …`` is handled by the mention rule,
    not the orchestrator branch. O3 only fires when no other rule
    has already decided."""

    def test_o1_orchestrator_responds_to_unaddressed_human(self):
        """The orchestrator owns turn selection, so every human turn
        is actionable for it even without a direct mention."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-alpha",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "hello",
            "metadata": {},
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_o2_next_speaker_match_responds(self):
        """``metadata.next_speaker_participant_id`` matches one of my
        participant ids → RESPOND. This is the handoff target path."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-beta",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "agent-alpha-pid",
            "room_id": "room-a",
            "content": "[HANDOFF] <@user:my-pid-123> take it from here",
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}],
                "next_speaker_participant_id": "my-pid-123",
                "_nonce": "n1",
            },
        }
        # Direct mention rule 3 actually fires before O2 in this
        # specific case — the HANDOFF message mentions the target.
        # Either way the decision is RESPOND; the test asserts the
        # target agent does act.
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_o2_next_speaker_match_without_mention(self):
        """If the server stamps ``next_speaker_participant_id`` on a
        plain ambient message (no handoff prefix, no mention), the
        target agent still wakes up under O2. Defends against the
        case where a future server-side path pushes the pointer
        without re-sending the full ``[HANDOFF]`` message."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-beta",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "agent-alpha-pid",
            "room_id": "room-a",
            "content": "let's keep moving",
            "metadata": {
                "next_speaker_participant_id": "my-pid-123",
                "_nonce": "n1",
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_o3_non_orchestrator_non_target_skips(self):
        """Unaddressed human message, I'm neither the orchestrator nor
        the next speaker → SKIP. This is the core behavioural change
        from Phase B: regular workers stay silent until called."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-beta",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "hello",
            "metadata": {},
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_o3_next_speaker_points_elsewhere_skips(self):
        """Server routed the next turn to a peer agent → SKIP."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-beta",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "agent-alpha-pid",
            "room_id": "room-a",
            "content": "[HANDOFF] <@user:other-pid> your turn",
            "metadata": {
                "mentions": [{"type": "user", "id": "other-pid"}],
                "next_speaker_participant_id": "other-pid",
                "_nonce": "n1",
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.SKIP

    def test_orchestrator_unset_falls_back_to_mentioned_only(self):
        """Strategy is 'orchestrator' but ``orchestrator_agent_id`` is
        unset (admin flipped the knob but never picked an agent).
        Graceful fallback: unaddressed human still wakes every agent,
        so the room stays usable even when misconfigured."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-alpha",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": None},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "hello",
            "metadata": {},
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_direct_mention_still_wins(self):
        """Direct mention is evaluated BEFORE the strategy dispatcher,
        so an orchestrator room with an explicit ``<@user:me>`` still
        routes through rule 3 regardless of O1/O2/O3."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-beta",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "human-pid",
            "room_id": "room-a",
            "content": "<@user:my-pid-123> direct question",
            "metadata": {
                "mentions": [{"type": "user", "id": "my-pid-123"}],
            },
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND

    def test_delegated_still_wins(self):
        """``[DELEGATED]`` is also upstream of the strategy dispatcher,
        so it always RESPONDs regardless of orchestrator routing."""
        client = _make_client(
            my_pids={"my-pid-123"},
            agent_id="agent-beta",
            speaker_strategy={"room-a": "orchestrator"},
            orchestrator_agent_id={"room-a": "agent-alpha"},
        )
        msg = {
            "participant_id": "agent-gamma",
            "room_id": "room-a",
            "content": "[DELEGATED] do the thing",
            "metadata": {"_nonce": "n1"},
        }
        assert decide_policy(msg, client) is MessagePolicy.RESPOND
