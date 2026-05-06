# CHANGELOG


## Unreleased

## v0.6.0 (2026-05-06)

### Features — per-agent permission level (#309)

- 3-tier permission model wired through the agent runtime
  ([#310](https://github.com/e7217/doorae/pull/310)).

### Fixes

- Expose cluster MCP tools to all engines + status enum / UI parity
  ([#321](https://github.com/e7217/doorae/pull/321)).

## v0.5.1 (2026-04-28)

### Workspace bump

- Workspace-consistent version bump alongside `doorae-machine` 0.5.1
  (Windows `secure_chmod` `DELETE` rights fix). No functional changes
  in agent.

## v0.5.0 (2026-04-28)

### Features — Windows native support (#300)

- gemini_cli engine adapter switches from POSIX-only `killpg` /
  `SIGKILL` to `proc_kill.terminate_tree` (psutil) for cross-platform
  process tree termination
  ([#301](https://github.com/e7217/doorae/pull/301)).
- Add `psutil` dependency.

## v0.4.1 (2026-04-28)

### Features — collaboration mode wiring (#279)

- Lift `_build_roster_suffix` from the claude_code adapter to
  `ChatClient.compose_roster_suffix(...)` so codex and gemini_cli
  share the same logic. Adds an optional
  `with_collaborative_hint` flag that appends a peer-mention usage
  paragraph instructing the agent to delegate via `<@user:UUID>`
  syntax and synthesize the replies.
- Cache `my_collaboration_mode` per room from welcome frames; the
  three engine adapters consult it when assembling the LLM system
  prompt. Solo agents see the prompt unchanged; collaborative
  agents receive the roster + hint even when they are not the
  room's orchestrator.
- Wrap drained pending context (ambient room messages) in a
  `<room_conversation>` XML block before injecting into the LLM
  prompt (#284). The Korean preamble explicitly tells the model the
  block is awareness context — already visible to the user — and
  not to relay or summarize it. Empty buffers short-circuit so
  pre-#284 solo turns stay byte-identical. Applied uniformly to
  claude_code, codex, and gemini_cli adapters.

### Refactor — centralize user-content augmentation (#286)

- Promote the drain → wrap → concat pipeline from the three session
  adapters (claude_code, codex, gemini_cli) up to
  `EngineAdapter.assemble_user_content(room_id, raw)`. The previous
  wave of changes (#279 / #283 / #284) had to touch all three
  adapters identically; future augmentations now land in one place.
  Conversion result is byte-identical — no behavioural change.

### Refactor — centralize memory/roster injection (#293)

- Lift memory/roster injection from per-engine adapters to the
  `EngineAdapter` base
  ([#295](https://github.com/e7217/doorae/pull/295)).

### Fix — separate mention-as-routing from mention-as-reference (#288)

- Stop emitting raw `<@user:UUID>` routing tokens in the roster
  suffix. Live tokens encouraged the LLM to copy them into prose
  when merely recommending or comparing peers, which the server
  parsed as actionable mentions and woke unintended agents. The
  roster now lists peers as `display_name (id: UUID, kind: ...)`
  with a header that explicitly says to use display names in
  prose and construct `<@user:PARTICIPANT_ID>` only when
  intentionally calling a peer. The collaborative usage hint
  reinforces the same rule with a "never put a routing token in
  prose that merely mentions or lists peers" line. orchestrator
  `handoff_to` MCP calls and user-side mention parsing are
  unaffected.

### Fix — collaborative synthesis opt-in (#283)

- Make collaborative synthesis opt-in rather than mandatory.

### Engines

- Add GPT-5.5 to codex/openai catalog and bump default
  ([#267](https://github.com/e7217/doorae/pull/267)).

### Chores

- Remove dead engine adapters (openai, anthropic, openhands,
  deep-agents) ([#294](https://github.com/e7217/doorae/pull/294)).

## v0.4.0 (2026-04-25)

### Features — orchestrator & speaker strategies (#159)

- Speaker-strategy schema + welcome propagation
  ([#164](https://github.com/e7217/doorae/pull/164))
  — Phase A introduces the strategy contract that
  downstream phases plug into.
- Strategy dispatcher + `round_robin`
  ([#168](https://github.com/e7217/doorae/pull/168))
  — Phase B routes a turn through pluggable speaker
  selectors.
- Orchestrator agent + `handoff` tool + per-agent token
  UI ([#178](https://github.com/e7217/doorae/pull/178))
  — Phase C+D adds an orchestrator role that selects the
  next speaker via a deterministic tool-call rather than
  free-form prose; surfaces participant roster on
  `handoff_to` and broadcasts room settings
  ([#224](https://github.com/e7217/doorae/pull/224)).

### Features — multi-session DM & shared file memory

- Per-agent multi-session DM + cross-engine file memory +
  ephemeral mode
  ([#240](https://github.com/e7217/doorae/pull/240))
  — same agent can hold multiple parallel DM threads with
  isolated history; ephemeral mode skips persistence.
- Room shared files copy-distributed to agent memory
  ([#250](https://github.com/e7217/doorae/pull/250))
  — files attached to a room are copied into each agent's
  workspace under `memory/shared/` on spawn and re-synced
  on attach/detach.
- Bridge `memory/shared/` into agent workspace
  ([#260](https://github.com/e7217/doorae/pull/260))
  — workspace now exposes `memory/shared/` as a stable
  cross-engine path.

### Features — ambient context window (#74, #148)

- Decouple context ingestion from response gate (#74)
  ([#139](https://github.com/e7217/doorae/pull/139))
- Ambient context window for session engines (#74 Stage B)
  ([#141](https://github.com/e7217/doorae/pull/141))
- Per-room `context_window_enabled` toggle (#148 Part 1)
  & per-agent `context_window_opt_out` (Part 2) +
  `ingest_only` broadcast wiring (Part 3) +
  Stage B accumulator removed (Part 4)
  ([#149](https://github.com/e7217/doorae/pull/149),
  [#150](https://github.com/e7217/doorae/pull/150),
  [#151](https://github.com/e7217/doorae/pull/151),
  [#152](https://github.com/e7217/doorae/pull/152)).

### Features — task-init guards & cycle detection (#157)

- Guard task-init reset-prefix abuse (Phase A)
  ([#160](https://github.com/e7217/doorae/pull/160))
- Detect semantic cycles in `decide_policy` (Phase B)
  ([#161](https://github.com/e7217/doorae/pull/161))

### Features — LLM gateway wiring (#197 Phase 5)

- Agent reads/writes through the embedded LiteLLM gateway
  ([#209](https://github.com/e7217/doorae/pull/209))
  — closes the gateway loop opened in Phases 1–4.

### Features — observability

- Explicit request lifecycle + orphan sweeper for
  observability (#204)
  ([#210](https://github.com/e7217/doorae/pull/210))

### Features — MCP

- Auto-approve MCP tool calls for all engines (#134)
  ([#137](https://github.com/e7217/doorae/pull/137))

### Fixes

- `agent/gemini`: pass `--skip-trust` to bypass the
  trusted-folders gate (#261)
  ([#262](https://github.com/e7217/doorae/pull/262))
- Sync room shared files on agent respawn & mid-session
  (#255) ([#256](https://github.com/e7217/doorae/pull/256))
- Bypass `ingest_only` stamp for human senders; move
  orchestrator O1 ahead of stamp (#233)
  ([#235](https://github.com/e7217/doorae/pull/235))
- Deliver codex responses that span long tool turns (#190)
  ([#194](https://github.com/e7217/doorae/pull/194))
- Keep `engine_secrets` out of agent
  `/proc/self/environ` (#184 follow-up)
  ([#193](https://github.com/e7217/doorae/pull/193))
- Include source/responder `display_name` in
  `room_query` / `room_query_result` metadata (#155, #153)
  ([#154](https://github.com/e7217/doorae/pull/154),
  [#156](https://github.com/e7217/doorae/pull/156))

### Refactors

- Remove `codex-extra` virtual engine
  ([#258](https://github.com/e7217/doorae/pull/258))
- Inject `engine_secrets` via subprocess env, not disk
  `.env` (#184)
  ([#189](https://github.com/e7217/doorae/pull/189))


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
