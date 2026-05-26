# CHANGELOG


## Unreleased

## v0.8.0 (2026-05-22)

### ⚠ Breaking changes — full anygarden rebrand

This release replaces every remaining `doorae` reference with
`anygarden`. **No backward-compatibility shim is provided.**
End users and operators must update:

- **Python imports**: `from doorae … import X` → `from anygarden … import X`
- **CLI commands**: `doorae-server` → `anygarden-server`
- **Environment variables** (~30 vars): `DOORAE_*` → `ANYGARDEN_*`
  (`DOORAE_JWT_SECRET`, `DOORAE_TOKEN`, `DOORAE_LLM_GATEWAY_*`,
  `DOORAE_DEV`, etc.)
- **Data directory**: `~/.doorae/` → `~/.anygarden/`
  (jwt_secret, mcp_secrets_key, agents, machine, db all moved)
- **SQLite default URL**: `~/.doorae/doorae.db` → `~/.anygarden/anygarden.db`
- **Frontend**: localStorage keys (`doorae_token`, `doorae_is_guest`, …)
  → `anygarden_*` — all existing sessions invalidated, users will need
  to re-login.

See PR #394 for the full rebrand diff.

### Source layout

- `packages/cluster/doorae/` → `packages/cluster/anygarden/`
  (per-package directory rename matches the distribution name).

## v0.7.1 (2026-05-21)

### Release infrastructure

- **Renamed PyPI distribution** from `drhub` to `anygarden` — service
  rebrand to anygarden. `drhub` 0.7.0 will be yanked after this
  release publishes on PyPI. Python import path (`anygarden`), CLI
  command (`anygarden-server`), source directory, and CHANGELOG history
  unchanged.

## v0.7.0 (2026-05-20)

### Release infrastructure

- **Renamed PyPI distribution** from `anygarden-cluster` to `drhub`
  ([#387](https://github.com/e7217/anygarden/pull/387)). Python import
  path, CLI command (`anygarden-server`), source directory, and
  CHANGELOG history unchanged.

### Features

- OpenHands V1 SDK migration Phases 0–6 — in-process Python agent
  runtime alternative to CLI engines
  ([#355](https://github.com/e7217/anygarden/pull/355),
  [#356](https://github.com/e7217/anygarden/pull/356)).
- Wire `engine_secrets` + gateway model merge for OpenHands Ollama
  path ([#359](https://github.com/e7217/anygarden/pull/359),
  [#361](https://github.com/e7217/anygarden/pull/361)).
- Mark `claude-code` engine deprecated; admin UI surfaces a
  Deprecated badge with migration hint
  ([#382](https://github.com/e7217/anygarden/pull/382),
  [#388](https://github.com/e7217/anygarden/pull/388)).
- Shared file references in chat — inline highlighting + sidebar
  ([#376](https://github.com/e7217/anygarden/pull/376),
  [#378](https://github.com/e7217/anygarden/pull/378)).
- Sidebar unread update indicators
  ([#385](https://github.com/e7217/anygarden/pull/385),
  [#386](https://github.com/e7217/anygarden/pull/386)).
- Expose machine online status on agent rows
  ([#383](https://github.com/e7217/anygarden/pull/383),
  [#384](https://github.com/e7217/anygarden/pull/384)).

### Fixes

- **Orchestrator strategy: server-side fallback nominate** when
  the moderator LLM omits handoff/mention tokens — round-robin
  rotation prevents silent room stalls
  ([#389](https://github.com/e7217/anygarden/pull/389)).
- Auto-reset OpenHands agents on cluster startup
  ([#379](https://github.com/e7217/anygarden/pull/379),
  [#380](https://github.com/e7217/anygarden/pull/380)).
- OpenHands runtime tools registration, gateway provider rewrite,
  detector visibility
  ([#377](https://github.com/e7217/anygarden/pull/377)).
- Increase litellm health-probe timeout (10s → 30s)
  ([#362](https://github.com/e7217/anygarden/pull/362),
  [#363](https://github.com/e7217/anygarden/pull/363)).
- Add `llm_gateway_binary` config knob to escape PATH-shadowed
  bare `litellm` ([#364](https://github.com/e7217/anygarden/pull/364),
  [#365](https://github.com/e7217/anygarden/pull/365)).
- Prevent stale auth token websocket reconnect loops
  ([#371](https://github.com/e7217/anygarden/pull/371)).
- Cache `anygarden_token` per-agent so `sync_batch` frame rebuilds
  don't orphan it ([#369](https://github.com/e7217/anygarden/pull/369),
  [#370](https://github.com/e7217/anygarden/pull/370)).

### Docs

- README overview + Mermaid "How It Works" diagram
  ([#381](https://github.com/e7217/anygarden/pull/381)).
- Deep-research note documenting multi-agent turn-taking mediator
  failure modes and mitigation roadmap
  (`docs/research/2026-05-12-multi-agent-turn-taking-mediator-failure.md`).

## v0.6.0 (2026-05-06)

### Features — autonomous responsibility & Goals UI (#302)

- Right context rail — Tasks/Files sidebar 통합 (Phase 1)
  ([#306](https://github.com/e7217/anygarden/pull/306)).
- Autonomous responsibility MVP — Goal scheduler + executor
  (Phase 2) ([#307](https://github.com/e7217/anygarden/pull/307)).
- Goals UI — right rail Responsibilities +
  AgentSettingsDialog (Phase 3)
  ([#308](https://github.com/e7217/anygarden/pull/308)).

### Features — per-agent permission level (#309)

- 3-tier permission model + codex sandbox dial (PR-A,
  [#310](https://github.com/e7217/anygarden/pull/310)).
- gemini + claude-code permission mappings + topology ⚠ + activity
  surface (PR-B, [#311](https://github.com/e7217/anygarden/pull/311)).

### Features — task auto-routing

- Auto-rep invariant + assignee picker in right rail
  ([#315](https://github.com/e7217/anygarden/pull/315)).
- Batch auto-route unassigned tasks via room representative
  ([#316](https://github.com/e7217/anygarden/pull/316)).

### Features — right rail polish (#329)

- Density polish — wider rail + unified assignee slot + split goals
  meta ([#324](https://github.com/e7217/anygarden/pull/324)).
- Viewport-driven default + width staging (Phase 1 of #329,
  [#330](https://github.com/e7217/anygarden/pull/330)).
- Stage agent message + file-chip widths (Phase 2 of #329,
  [#331](https://github.com/e7217/anygarden/pull/331)).
- Absorb search + artifacts entries into RoomHeader (Phase 3 of
  #329, [#332](https://github.com/e7217/anygarden/pull/332)).
- Hide header search below `sm` + add menu fallback (Phase 4 of
  #329, [#333](https://github.com/e7217/anygarden/pull/333)).

### Features — tasks UI

- `TasksPanel` 섹션 접기 + terminal 정리 + status 표시 누락 수정
  ([#322](https://github.com/e7217/anygarden/pull/322)).

### Fixes

- Expose cluster MCP tools to all engines + status enum/UI parity
  ([#321](https://github.com/e7217/anygarden/pull/321)).
- Renumber `038_task_assigned_at` → `039` to unbreak main
  ([#318](https://github.com/e7217/anygarden/pull/318)).
- Broadcast scheduler-injected task assignments + add stuck task
  sweeper ([#317](https://github.com/e7217/anygarden/pull/317)).
- Align right rail row right-edge with section headers
  ([#326](https://github.com/e7217/anygarden/pull/326)).
- Right rail hover text truncation — `appearance-none` + wider slot
  + opaque action backdrop
  ([#328](https://github.com/e7217/anygarden/pull/328)).
- Contain right rail task row overflow
  ([#335](https://github.com/e7217/anygarden/pull/335)).
- Contain right rail viewport overflow at the substrate
  ([#337](https://github.com/e7217/anygarden/pull/337)).
- Bump task pickup timeout and harden status directive
  ([#339](https://github.com/e7217/anygarden/pull/339)).

## v0.5.1 (2026-04-28)

### Workspace bump

- Workspace-consistent version bump alongside `anygarden-machine` 0.5.1
  (Windows `secure_chmod` `DELETE` rights fix). No functional changes
  in cluster.

## v0.5.0 (2026-04-28)

### Features — Windows native support (#300)

- Consolidate POSIX-only `os.chmod` / `Path.chmod` call sites
  (jwt_secret, mcp_secrets_key, litellm config) onto
  `safefs.secure_chmod` so cluster runs natively on Windows 10/11
  ([#301](https://github.com/e7217/anygarden/pull/301)). On Windows the
  helper writes a DACL granting only the current process owner the
  modeled rights, instead of `os.chmod`'s POSIX no-op.

## v0.4.1 (2026-04-28)

### Features — agent → room artifact pipeline (#290 Phase B)

- Agents emit artifacts that propagate into the originating room
  ([#296](https://github.com/e7217/anygarden/pull/296)).
- Render ANSI escapes inside fenced code blocks
  ([#291](https://github.com/e7217/anygarden/pull/291)).

### Features — tasks

- Agent auto-execution + dual room/agent views
  ([#268](https://github.com/e7217/anygarden/pull/268)).
- Orchestrator `create_task` MCP tool
  ([#272](https://github.com/e7217/anygarden/pull/272)).
- `/task` slash command in chat input
  ([#273](https://github.com/e7217/anygarden/pull/273)).
- Embed `mark_task_status` self-instruction in synthetic mention
  ([#276](https://github.com/e7217/anygarden/pull/276)).

### Features — per-agent collaboration mode (#279)

- Add `agents.collaboration_mode` enum (`solo` | `collaborative`,
  default `solo`) so admins can flip an agent into "delegate via peer
  mention" without piling another enum onto the `rooms` table. The
  agent SDK reads this via the welcome frame's
  `my_collaboration_mode` slot and appends a usage hint to the LLM
  system prompt; pre-#279 behaviour is byte-identical for solo agents.
- Server-side peer-mention safety net: every agent message that
  targets another agent participant gets stamped with
  `metadata.peer_depth` and `metadata.kind`
  (`peer_query`/`peer_response`); a per-room `PeerHandoffBudget`
  resets on each human/guest send and trips on `MAX_PEER_DEPTH`
  (1 layer) or `MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN` (8 events).
  Mentions over the cap are stripped from the broadcast content
  while the prose answer flows through
  ([#280](https://github.com/e7217/anygarden/pull/280)).

### Features — MCP & engines

- Auto-register anygarden self-MCP via Streamable HTTP
  ([#278](https://github.com/e7217/anygarden/pull/278)).
- Expose agent description for cross-agent recognition
  ([#274](https://github.com/e7217/anygarden/pull/274)).
- Add GPT-5.5 to codex/openai catalog and bump default
  ([#267](https://github.com/e7217/anygarden/pull/267)).

### Fixes

- Rebase `room_artifacts` migration onto 035 to remove cross-branch
  conflict ([#297](https://github.com/e7217/anygarden/pull/297)).
- Derive `AgentSettingsDialog` agent prop from live agents list to
  prevent stale snapshot after in-dialog edits
  ([#282](https://github.com/e7217/anygarden/pull/282)).

### Chores

- Remove dead engine adapters (openai, anthropic, openhands,
  deep-agents) ([#294](https://github.com/e7217/anygarden/pull/294)).

## v0.4.0 (2026-04-25)

### Features — embedded LLM gateway (#197)

- Phase 1 — architecture docs
  ([#200](https://github.com/e7217/anygarden/pull/200))
- Phase 2 — backend supervisor, proxy, bootstrap
  ([#202](https://github.com/e7217/anygarden/pull/202))
- Phase 3 — admin REST API
  ([#207](https://github.com/e7217/anygarden/pull/207))
- Phase 4 — admin frontend with secondary sidebar
  ([#208](https://github.com/e7217/anygarden/pull/208))
- Phase 5 — agent wiring closes the loop
  ([#209](https://github.com/e7217/anygarden/pull/209))
- Surface `api_base` + `vllm` provider for local LLMs
  (#249) ([#251](https://github.com/e7217/anygarden/pull/251))
- Add `codex-extra` virtual engine for LiteLLM-routed
  agents ([#254](https://github.com/e7217/anygarden/pull/254));
  later removed in
  [#258](https://github.com/e7217/anygarden/pull/258).
- Restrict `/api/v1/llm/*` to agent + machine identities
  ([#212](https://github.com/e7217/anygarden/pull/212))
- Use LiteLLM liveliness health probe
  ([#252](https://github.com/e7217/anygarden/pull/252))

### Features — skill library (#119, #120, #123–#126, #133)

- Skill library with GitHub-based registration
  ([#121](https://github.com/e7217/anygarden/pull/121))
- Pass through full skill directory into agent spawn
  ([#127](https://github.com/e7217/anygarden/pull/127))
- Approve workflow + audit log
  ([#129](https://github.com/e7217/anygarden/pull/129))
- Agent self-authoring skills via MCP `create_skill` tool
  ([#130](https://github.com/e7217/anygarden/pull/130))
- `skills.sh` search proxy + stale check
  ([#131](https://github.com/e7217/anygarden/pull/131))
- Surface attached library skills in manifest dialog
  ([#136](https://github.com/e7217/anygarden/pull/136))
- Bump agent generation on skill
  attach/detach/delete/update
  ([#122](https://github.com/e7217/anygarden/pull/122))

### Features — MCP server templates (#124)

- Builtin + custom template catalog
  ([#128](https://github.com/e7217/anygarden/pull/128))
- Simplify custom template editor UI
  ([#196](https://github.com/e7217/anygarden/pull/196))
- Show/hide toggle for env value inputs in attach dialog
  ([#201](https://github.com/e7217/anygarden/pull/201))
- Restore horizontal focus ring on input focus
  ([#198](https://github.com/e7217/anygarden/pull/198))

### Features — orchestrator & speaker strategies (#159)

- Speaker-strategy schema + welcome propagation (Phase A)
  ([#164](https://github.com/e7217/anygarden/pull/164))
- Strategy dispatcher + `round_robin` (Phase B)
  ([#168](https://github.com/e7217/anygarden/pull/168))
- Orchestrator + handoff tool + per-agent token UI
  (Phase C+D) ([#178](https://github.com/e7217/anygarden/pull/178))
- Surface participant roster to `handoff_to` and
  broadcast room settings
  ([#224](https://github.com/e7217/anygarden/pull/224))
- Render orchestrator `[HANDOFF]` messages as
  breathing-border cards
  ([#239](https://github.com/e7217/anygarden/pull/239))

### Features — agent settings dialog

- Customizable avatars (emoji/lucide) + per-agent
  settings menu
  ([#104](https://github.com/e7217/anygarden/pull/104))
- Unify avatar/manifest/rooms/activity into single
  settings dialog
  ([#163](https://github.com/e7217/anygarden/pull/163))
- Stack sections on a single page; tighten spacing;
  divider + card refinements
  ([#166](https://github.com/e7217/anygarden/pull/166),
  [#169](https://github.com/e7217/anygarden/pull/169),
  [#171](https://github.com/e7217/anygarden/pull/171),
  [#173](https://github.com/e7217/anygarden/pull/173))
- Model + reasoning_effort editing in Settings Overview
  ([#218](https://github.com/e7217/anygarden/pull/218))

### Features — sidebar UX

- Collapse/expand sidebar on desktop
  ([#108](https://github.com/e7217/anygarden/pull/108))
- Hoist desktop collapse state into shared provider
  ([#117](https://github.com/e7217/anygarden/pull/117))
- Apply `AgentSettingsMenu` to admin agent DM items
  ([#107](https://github.com/e7217/anygarden/pull/107))
- Hide room-management UI in agent DMs
  ([#118](https://github.com/e7217/anygarden/pull/118))

### Features — topology

- Agent node redesign with name + engine logo + running
  pulse ([#86](https://github.com/e7217/anygarden/pull/86))
- Highlight rooms with active typing
  ([#88](https://github.com/e7217/anygarden/pull/88))
- Per-user draggable node positions with localStorage
  persistence ([#236](https://github.com/e7217/anygarden/pull/236))
- Merge `represents` edge into `participates` flag
  ([#228](https://github.com/e7217/anygarden/pull/228))

### Features — multi-session DM & shared files

- Per-agent multi-session DM + cross-engine file memory +
  ephemeral mode
  ([#240](https://github.com/e7217/anygarden/pull/240))
- Room shared files copy-distributed to agent memory
  ([#250](https://github.com/e7217/anygarden/pull/250))

### Features — context window (#148)

- Per-room `context_window_enabled`
  ([#149](https://github.com/e7217/anygarden/pull/149))
- Per-agent `context_window_opt_out`
  ([#150](https://github.com/e7217/anygarden/pull/150))
- Wire `ingest_only` broadcast + agent opt-out
  ([#151](https://github.com/e7217/anygarden/pull/151))
- Flip `context_window_enabled` default to `true` and
  gate as admin-only
  ([#230](https://github.com/e7217/anygarden/pull/230))

### Features — observability

- Guard task-init reset-prefix abuse
  ([#160](https://github.com/e7217/anygarden/pull/160))
- Detect semantic cycles in `decide_policy`
  ([#161](https://github.com/e7217/anygarden/pull/161))
- Room token-stats API with per-agent breakdown
  ([#162](https://github.com/e7217/anygarden/pull/162))
- Explicit request lifecycle + orphan sweeper
  ([#210](https://github.com/e7217/anygarden/pull/210))
- Turn-level agent activity timeline
  ([#223](https://github.com/e7217/anygarden/pull/223))
- Surface `starting` / `stopping` transitional states
  ([#220](https://github.com/e7217/anygarden/pull/220))

### Features — engines

- Refresh catalog with CLI-verified 2026-04-21 lineup
  ([#216](https://github.com/e7217/anygarden/pull/216))

### Features — UI / avatars

- Seed-based `EntityAvatar` for agents, DMs,
  participants, messages
  ([#99](https://github.com/e7217/anygarden/pull/99))
- Thread agent engine through `ParticipantOut`
  ([#103](https://github.com/e7217/anygarden/pull/103))
- Upload/download agent manifest files from edit dialog
  ([#100](https://github.com/e7217/anygarden/pull/100))
- Skill-aware manifest tree with engine filter +
  script extensions
  ([#114](https://github.com/e7217/anygarden/pull/114))
- Unify `AGENTS.md` into agent manifest file tree
  ([#110](https://github.com/e7217/anygarden/pull/110))

### Fixes

- Sync room shared files on agent respawn & mid-session
  ([#256](https://github.com/e7217/anygarden/pull/256))
- Sidebar AGENTS Agent settings: surface Model/Reasoning
  dropdowns ([#248](https://github.com/e7217/anygarden/pull/248))
- Sidebar agent row button alignment + DM rename/delete
  menu + name tooltip + hover-hide count badge
  ([#242](https://github.com/e7217/anygarden/pull/242),
  [#244](https://github.com/e7217/anygarden/pull/244))
- Bypass `ingest_only` stamp for human senders; move
  orchestrator O1 ahead of stamp
  ([#235](https://github.com/e7217/anygarden/pull/235))
- Sync runtime-room-add with agent lifecycle
  ([#229](https://github.com/e7217/anygarden/pull/229))
- Topology: align representative edge shape with
  `participates` ([#232](https://github.com/e7217/anygarden/pull/232));
  eliminate node re-render flicker on hover
  ([#85](https://github.com/e7217/anygarden/pull/85)).
- Decouple agent DM rooms from project lifetime (#179)
  ([#180](https://github.com/e7217/anygarden/pull/180))
- Silence welcome-race disconnect traceback in
  `ws_room` ([#177](https://github.com/e7217/anygarden/pull/177))
- Run alembic migrate before `make dev`
  ([#175](https://github.com/e7217/anygarden/pull/175))
- Deliver codex responses that span long tool turns
  ([#194](https://github.com/e7217/anygarden/pull/194))
- Persist MCP Fernet key + refuse prod boot without one
  ([#140](https://github.com/e7217/anygarden/pull/140))
- Write `claude-code` MCP config to `.mcp.json`
  ([#143](https://github.com/e7217/anygarden/pull/143))
- Admin dialog CSS overflow + focus-ring clipping
  ([#135](https://github.com/e7217/anygarden/pull/135))
- Emit UTC-aware ISO datetimes so KST clients don't
  shift by 9h
  ([#95](https://github.com/e7217/anygarden/pull/95))
- Dismiss historical chips and badge in-flight question
  bubbles ([#96](https://github.com/e7217/anygarden/pull/96))
- Include source/responder `display_name` in
  `room_query` / `room_query_result` metadata
  ([#154](https://github.com/e7217/anygarden/pull/154),
  [#156](https://github.com/e7217/anygarden/pull/156))

### Refactors

- Remove `codex-extra` virtual engine
  ([#258](https://github.com/e7217/anygarden/pull/258))
- Wrap Settings dialog sections in cards on warm-white
  body / restore whisper divider / tighten spacing
  ([#169](https://github.com/e7217/anygarden/pull/169),
  [#171](https://github.com/e7217/anygarden/pull/171),
  [#173](https://github.com/e7217/anygarden/pull/173))


## v0.3.2 (2026-04-17)

### Fixes — single-session WS

- Enforce single connection per `participant_id`
  ([#79](https://github.com/e7217/anygarden/issues/79),
  [#80](https://github.com/e7217/anygarden/pull/80))
  — when two clients shared an agent token (e.g.
  `anygarden-machine` reconcile racing a manual launch) both
  sockets stayed in `_rooms[room_id]` and every broadcast
  doubled up: duplicate `[ROOM_QUERY]` forwards, duplicate
  DM/mention replies, double LLM cost. The
  `representative_agent_id` guard from #61 only handled
  *different* agents, not multi-instance of the same one.
  `ConnectionManager.subscribe()` now evicts the prior
  subscription and closes the old socket with WS code `4040`
  ("superseded") before installing the new one.


## v0.3.1 (2026-04-16)

### Fixes — room-query banner

- Drop orphan pending `room_query` chips after a 7-minute TTL
  ([#66](https://github.com/e7217/anygarden/issues/66),
  [#68](https://github.com/e7217/anygarden/pull/68))
  — when a representative agent dies before `COLLECT_TIMEOUT`
  elapses, no `room_query_result` is ever emitted and the
  pending banner chip would otherwise become a permanent ghost
  (React-local `dismissedIds` doesn't survive refresh). The
  derivation is now factored into `src/lib/pending-queries.ts`
  with unit tests; stale pendings older than the TTL are
  filtered out in `buildPendingQueries`.


## v0.3.0 (2026-04-16)

### Features — room-query UX (#55)

- Structured room-query UX with banner chips and result cards
  ([#55](https://github.com/e7217/anygarden/issues/55),
  [#59](https://github.com/e7217/anygarden/pull/59))
  — source-room banner transitions pending → completed/timeout
  by `query_id`; target-room forward bubble gets a source badge;
  original room renders a collapsible result card per agent
  response. Server stamps `room_query` / `room_query_forward` /
  `room_query_result` metadata; no new WS frame types.

### Features — presence (#54)

- Unify agent liveness via `PresenceService` + UI indicator
  ([#54](https://github.com/e7217/anygarden/issues/54),
  [#60](https://github.com/e7217/anygarden/pull/60))
  — single read-through service for "is this participant
  responsive right now?" backed by `ConnectionManager` (truth)
  with `Agent.last_heartbeat_at` fallback. `GET /rooms/{id}`
  exposes `online` + `last_seen_at`; WS broadcasts
  `presence_update` frames. `[ROOM_QUERY]` `expected_count` now
  excludes offline agents so stale participants don't force a
  timeout.

### Features — sidebar

- Drag-and-drop reorder for pinned rooms in sidebar
  ([#47](https://github.com/e7217/anygarden/issues/47),
  [#51](https://github.com/e7217/anygarden/pull/51))
- Hover `...` menu for rename + delete room
  ([#46](https://github.com/e7217/anygarden/pull/46),
  [#48](https://github.com/e7217/anygarden/pull/48))

### Features — rooms

- Delete-room UI + tighten authz + WS broadcast
  ([#45](https://github.com/e7217/anygarden/pull/45))
  — owner/admin-only DELETE endpoint, cascade cleanup,
  `room_deleted` WS frame so other sessions drop the room
  without a refetch round-trip.

### Fixes — room routing

- Route direct-typed `#RoomName` mentions to the target room
  ([#53](https://github.com/e7217/anygarden/issues/53),
  [#57](https://github.com/e7217/anygarden/pull/57))
  — frontend now converts plain `#Name` text to the
  `<#room:id>` token before sending when the name matches
  exactly one known room, so typed mentions route the same as
  autocomplete-selected ones. Duplicate-name / unknown-name
  fallbacks preserved.
- Unify participant membership + `JoinRoomOut` broadcast
  ([#50](https://github.com/e7217/anygarden/issues/50),
  [#52](https://github.com/e7217/anygarden/pull/52))
  — the auto-join of a representative agent now emits a
  `JoinRoomOut` frame on every relevant WS session so the SDK
  subscribes to the new room in time for the upcoming broadcast
  (race that previously caused `(1/N)` miscounts in
  `[ROOM_QUERY]`).
- Break the `[ROOM_QUERY]` forwarding loop
  ([#42](https://github.com/e7217/anygarden/pull/42))
  — the server no longer re-attaches `room_query` metadata to
  agent-originated forwards; combined with the SDK's
  `<#room:…>` strip, the ad-infinitum recipient-forwards-again
  loop is closed at the source.
- Unify REST `metadata` field + prevent duplicate
  `room_query_forward` ([#61](https://github.com/e7217/anygarden/pull/61),
  [#62](https://github.com/e7217/anygarden/pull/62))
  — REST `MessageOut` now returns `metadata` (was
  `extra_metadata`) so history-loaded messages render the
  forward / result cards identically to WS-arrived ones.
  Target-room forwards are now emitted by the target room's
  representative only, not every agent that saw the question.
- Add `min-h-0` to ChatArea wrapper to restore inner scroll
  ([#63](https://github.com/e7217/anygarden/pull/63),
  [#64](https://github.com/e7217/anygarden/pull/64))

### Features — admin

- Allow admins to remove room participants
  ([#40](https://github.com/e7217/anygarden/pull/40))


## v0.2.0 (2026-04-15)

### Features — anonymous guest participation (RFC #22)

- Allow anonymous guest rows on users table
  ([#24](https://github.com/e7217/anygarden/pull/24))
- Room invite links with admin-only lifecycle
  ([#25](https://github.com/e7217/anygarden/pull/25))
- Guest identity + /auth/guest + forbid_guest gate
  ([#26](https://github.com/e7217/anygarden/pull/26))
- Guest branch in the WebSocket send path
  ([#27](https://github.com/e7217/anygarden/pull/27))
- Trim the guest read surface
  ([#28](https://github.com/e7217/anygarden/pull/28))
- Guest lifecycle job + metrics + final docs
  ([#31](https://github.com/e7217/anygarden/pull/31))

### Features — membership / UI

- Notify agent of dynamic room join via add_participant
  ([#17](https://github.com/e7217/anygarden/pull/17))
- Notify user on add_participant via WS
  ([#19](https://github.com/e7217/anygarden/pull/19))
- Show room participant list in a header popover
  ([#32](https://github.com/e7217/anygarden/pull/32))
- Allow admins to remove room participants
  ([#40](https://github.com/e7217/anygarden/pull/40))

### Fixes

- Machine deletion cascade and error surfacing
  ([#1](https://github.com/e7217/anygarden/pull/1))
- Delete agent's DM room when the agent is deleted
  ([#12](https://github.com/e7217/anygarden/pull/12))

### Docs

- WS frame tables in §1.5 synced with protocol.py
  ([#21](https://github.com/e7217/anygarden/pull/21))
- Anonymous guest participation RFC (design §11)
  ([#23](https://github.com/e7217/anygarden/pull/23))

## v0.1.0 (2026-04-14)

### Chores

- Switch license to Apache-2.0 and update author
  ([`a4f1d0a`](https://github.com/e7217/anygarden-cluster/commit/a4f1d0a8ddd6b1641dd08ed63c42f60b66576635))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Continuous Integration

- Add python-semantic-release for automatic versioning
  ([`eb5269a`](https://github.com/e7217/anygarden-cluster/commit/eb5269a8799737008802d19f9838470dddfce195))

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>

### Features

- Initial release — anygarden-cluster v0.2.0
  ([`47bed32`](https://github.com/e7217/anygarden-cluster/commit/47bed3254a656e208e1d765b6e8ece22707043f2))

Extracted from e7217/anygarden monorepo (formerly anygarden-server). Renamed package anygarden-server →
  anygarden-cluster.

Includes: - FastAPI chat server with WebSocket + REST API - SQLAlchemy async DB with Alembic
  migrations (11 versions) - Auth system (JWT, machine tokens, admin/owner roles) - Agent & machine
  management APIs - React/Vite frontend (SPA) - Prometheus observability - anygarden-machine dependency
  via GitHub source

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
