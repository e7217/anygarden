# CHANGELOG


## v0.3.2 (2026-04-17)

No code changes this cycle — version bumped to keep the three
monorepo packages aligned.


## v0.3.1 (2026-04-16)

### Fixes — agent turn counter

- Reset `_agent_turn_count` on self-emitted `[ROOM_QUERY]` /
  `[DELEGATED]` frames in agent-only rooms
  ([#67](https://github.com/e7217/doorae/issues/67),
  [#69](https://github.com/e7217/doorae/pull/69))
  — the hard/soft filter branches in
  `ChatClient._process_frame` previously incremented the turn
  counter and early-returned for self-sent / nonce-echo
  frames, so a representative's own forwards never reached the
  main-path reset. In human-less rooms the counter accumulated
  across task rounds and later agent replies were dropped at
  `max_agent_turns`. The reset now fires from both branches
  before the early-return.


## v0.3.0 (2026-04-16)

### Features — room-query forward & result metadata (#55)

- Emit structured `room_query_forward` / `room_query_result`
  metadata on forwarded questions and the synthesized summary
  ([#55](https://github.com/e7217/doorae/issues/55),
  [#59](https://github.com/e7217/doorae/pull/59))
  — `execute_room_query` now carries `source_room_id`,
  `source_participant_id`, and `query_id` on the `[ROOM_QUERY]
  …` forward; `_synthesize_and_deliver` attaches `responses`
  (participant_id + content) + `missing` so the source-room
  result card can render one collapsible block per agent.

### Features — presence-aware room query (#54)

- Exclude offline agents from `[ROOM_QUERY]` `expected_count`
  ([#54](https://github.com/e7217/doorae/issues/54),
  [#60](https://github.com/e7217/doorae/pull/60))
  — `execute_room_query` filters `agent_participants` by
  `online=True` so a dead agent no longer forces a timeout
  countdown; the missing-responder label now includes
  `(offline, last seen …)` when relevant.

### Fixes — mention / routing

- Break the `[ROOM_QUERY]` forwarding loop (SDK side)
  ([#42](https://github.com/e7217/doorae/pull/42))
  — representative agents strip the `<#room:…>` token before
  forwarding so the target-room recipients don't re-trigger a
  cross-room query on the same content.
- Prevent duplicate `room_query_forward` from multi-agent rooms
  ([#61](https://github.com/e7217/doorae/pull/61))
  — only the target room's representative emits the forward;
  other agents that happened to see the question no longer
  double-post.


## v0.2.0 (2026-04-15)

### Fixes — mention routing

- Respect explicit @mention when routing human messages
  ([#36](https://github.com/e7217/doorae/pull/36))
  — multi-agent rooms no longer fan out every reply; the
  unified ``should_respond`` gate consults the server-parsed
  ``metadata.mentions`` list and stays silent when another
  participant was addressed.
- Route id-based @mention tokens to the intended target
  ([#37](https://github.com/e7217/doorae/pull/37))
  — frontend autocomplete emits ``<@user:<participant_id>>``;
  the agent now matches that id against
  ``_my_participant_ids`` (exact set membership, no substring
  traps). Case-insensitive legacy-name match retained; content
  scan fallback tightened with a ``(?![\w:])`` lookahead so
  an agent literally named ``user`` is no longer false-matched
  by the id-token substring.

## v0.1.0 (2026-04-14)

### Chores

- Switch license to Apache-2.0 and update author
  ([`613986b`](https://github.com/e7217/doorae-agent/commit/613986b4cf30644928817a1b40f3578938e07888))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Continuous Integration

- Add python-semantic-release for automatic versioning
  ([`f51f2dd`](https://github.com/e7217/doorae-agent/commit/f51f2dd96f7c3b1c286a7449d8cf82a3efdfbaae))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Features

- Initial release — doorae-agent v0.1.0
  ([`3e6e66a`](https://github.com/e7217/doorae-agent/commit/3e6e66a9e5d34231fdf9a6d2e3d8e79c2649795a))

Extracted from e7217/doorae monorepo (formerly doorae-sdk). Renamed package doorae_sdk →
  doorae_agent for clarity.

Includes: - ChatClient (WebSocket + REST) - 6 engine adapters (OpenAI, Anthropic, Claude Code,
  Codex, Gemini CLI, Deep Agents) - CLI entry points (doorae-agent, doorae-client) - Agent profile
  system - Protocol frames & versioning

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
