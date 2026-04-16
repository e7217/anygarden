# feat(agent-ts): add TypeScript agent runtime alongside Python (#73)

- Commit: `2ff2c22` (2ff2c2286e37c860205117a8383c76b8571fd9ad)
- Author: Changyong Um
- Date: 2026-04-16T21:12:32+09:00
- PR: #73

## Situation

Doorae's agent runtime lived entirely in Python (`packages/agent/`). Three engines that ship first-party TypeScript SDKs — Claude Code, Codex, Gemini CLI — were being driven indirectly through CLI subprocess parsing or best-effort HTTP wrappers. The Claude Code adapter in particular had to shell out to the `claude` CLI from Python and scrape assistant text from its JSON stream, missing session features the TS SDK exposes natively (V2 `unstable_v2_createSession` / `resumeSession`, built-in `settingSources`, typed message streams).

Two earlier fixes — `#61` (welcome-frame `agent_id` for room_query gating) and `#67` (turn-counter reset on self-emitted `[ROOM_QUERY]`/`[DELEGATED]`) — lived only on the Python side; any parallel runtime had to preserve those semantics or reintroduce the bugs they fixed.

## Task

- Stand up a new `@doorae/agent-ts` package under `packages/agent-ts/` that speaks the same `doorae.v1` WS protocol, preserves every routing/coordination rule from `doorae-agent`, and hosts the Claude Code V2 preview SDK natively.
- Port `#67` across all three counter paths (hard filter / soft filter / main path) with an explicit regression test replaying the 4-round agent-only `[ROOM_QUERY]` fanout.
- Port the `#61` representative-agent gate for `room_query` metadata so non-representative TS agents don't duplicate forwards.
- Thread a `runtime: "python" | "typescript"` selector end-to-end: DB column (with alembic migration and `server_default='python'` to keep pre-#73 rows running on Python), API schemas (create/update/out), cluster scheduler `sync_desired_state`, machine `SpawnManifest`, and spawner branching.
- Make the machine daemon resolve `doorae-agent-ts` on PATH first, fall back to `npx -y @doorae/agent-ts`, and log which arm won (mirror the existing `uvx` fallback pattern on the Python arm).
- Keep the Python arm behaviourally unchanged so zero existing agents regress.
- MVP scope = Claude Code only; Codex and Gemini CLI adapters deferred.

## Action

- `package.json` (new, repo root) — npm workspaces `["packages/cluster/frontend", "packages/agent-ts"]` with `test:ts` / `build:ts` / `lint:ts` scripts. `pyproject.toml` gains `[tool.uv.workspace] exclude = ["packages/agent-ts"]` so uv doesn't fail on the missing pyproject.
- `packages/agent-ts/` scaffolding:
  - `package.json`: `@doorae/agent-ts` with bin `doorae-agent-ts`, pinned `@anthropic-ai/claude-agent-sdk@0.2.110` + zod v4 peer, `ws`, `pino`, `commander`. Dev: `vitest`, `tsup`, `typescript ~5.7`, `eslint` v9 flat.
  - `tsconfig.json`, `tsup.config.ts` (single-file `dist/cli.js` bundle + dts), `vitest.config.ts` (silent logs), `eslint.config.js` (flat, includes modern Node globals `fetch`/`AbortController`/`setImmediate`/`queueMicrotask`/etc.).
- `src/protocol/frames.ts` — zod discriminated unions mirroring `packages/cluster/doorae/ws/protocol.py` 1:1 (Send/Typing/CreateRoom/JoinRoom incoming; Message/RoomCreated/JoinRoom/RoomDeleted/RoomMembershipChanged/RoomPinOrderChanged/Typing/PresenceUpdate/Welcome/Error outgoing). `safeParseOutgoingFrame` for the WS read loop.
- `src/protocol/auth.ts` — `buildSubprotocols(token)` → `["doorae.v1", "bearer.<token>"]`, matching `doorae_agent.protocol.versioning`.
- `src/routing/turn-counter.ts` — `TurnCounter` class with `handleSelf`/`handleNonceEcho`/`handleIncoming`. Task-init reset (`[ROOM_QUERY]`/`[DELEGATED]`) applies on every path; `isTaskInitContent` is the single source of truth. `src/routing/nonce.ts` manages the sent-nonce set with `allocate()` + `consume()`.
- `src/routing/should-respond.ts` — full port of `base.py::should_respond`, including the word-or-colon lookahead `(?![\\w:])` that stops content-scan matching the `<@user:pid>` ID token, and the `#61` representative-agent gate.
- `src/client.ts` — `ChatClient` with per-room WS loop, exponential backoff (configurable `initialReconnectDelay`/`maxReconnectDelay`), `since_seq` recovery on reconnect, welcome-frame handling (stores `_my_participant_ids` + `agentId`, auto-joins `pending_rooms`), serial per-room frame dispatch. `_processFrame` reproduces the Python three-path counter behaviour and only dispatches `deliver` outcomes. `__testFeedFrame` / `__testSetConnection` exposed for hermetic tests.
- `src/coordination/room-query.ts` — `parseRoomQuery`, `stripRoomMention` (removes `<#room:…>` to prevent forwarding loops), `fmtAgo` (the `#54` human-readable offline timestamp), `executeRoomQuery` covering solo/completed/timeout with `deliverResult` building the same `[취합 결과]` body + `room_query_result` metadata shape as the Python `_deliver_result`. Multi-reply callback filters our own `[ROOM_QUERY]` ghost echoes.
- `src/coordination/delegate.ts` — `parseDelegate` handles `@mention /delegate <sub-room> <task>` (finds `/delegate` after any prefix), `executeDelegate` joins the sub-room, posts confirmation + `[DELEGATED]` task, and registers a one-shot reply callback with 5 min timeout.
- `src/engines/types.ts` — `EngineAdapter` interface (`start` / `onMessage` / `stop`).
- `src/engines/claude-code.ts` — `ClaudeCodeAdapter` using `unstable_v2_createSession({ model, cwd, settingSources: ["project", "user"], ... })` via a lazy, test-injectable `sdkLoader`. Per-room `SDKSession` map — first turn creates, follow-ups reuse the live session (the V2 handle replaces the Python `resume=<sid>` pattern). Message stream harvested: prefer `SDKResultMessage.result`, else concatenate `SDKAssistantMessage` text blocks, skip tool-use/thinking blocks. Optional `onStreamChunk` callback lets the CLI relay typing indicators.
- `src/cli.ts` — `commander`-based CLI. Reads `DOORAE_TOKEN` from env only (never argv). `--engine claude_code|codex|gemini_cli`; codex/gemini throw with a clear "out of scope for MVP (#73 phase 1)" error so misconfigured spawns fail loudly. Main handler composes `shouldRespond` → `parseDelegate`/`executeDelegate` → `parseRoomQuery`/`executeRoomQuery` → adapter, with a 2 s typing-ping loop around the LLM call. SIGINT/SIGTERM shutdown gracefully stops the adapter + client.
- `packages/machine/doorae_machine/spawner.py` — `SpawnManifest.runtime: str = "python"` added (backward-compatible default). `spawn()` branches on `runtime == "typescript"`: probes `shutil.which("doorae-agent-ts")`, falls back to `["npx", "-y", "@doorae/agent-ts", ...]`; emits `agent_binary_resolved` with `runtime`/`source`/`path` fields so operators can audit which binary ran.
- `packages/machine/doorae_machine/protocol/frames.py` — `SyncDesiredStateFrame.runtime: str = "python"`; `daemon.py` threads the value (via `getattr` for schema-version tolerance) into the built `SpawnManifest`.
- `packages/cluster/doorae/db/migrations/versions/016_agent_runtime.py` — alembic 016 adds `agents.runtime String(20) NOT NULL server_default='python'` inside `batch_alter_table` for SQLite compatibility. `downgrade()` drops the column; round-trip validated against local SQLite.
- `packages/cluster/doorae/db/models.py` — `Agent.runtime` mapped column (String(20), NOT NULL, default+server_default `'python'`).
- `packages/cluster/doorae/api/v1/agents.py` — `AgentCreate.runtime` (default `"python"`), `AgentUpdate.runtime` + `runtime_set` flag, `AgentOut.runtime` (read-only echo). `create_agent` stores the value; `update_agent` applies it and bumps generation so the machine respawns with the new runtime.
- `packages/cluster/doorae/scheduler/lifecycle.py` — `_build_sync_desired_state` includes `runtime: getattr(agent, "runtime", "python")` so the frame carries the selector to the machine.
- Tests:
  - TS: `tests/protocol.test.ts` (18), `tests/client.test.ts` (20), `tests/turn-counter.test.ts` (18 — includes a 4-round agent-only fanout regression reproducing `#67` at `max_agent_turns=3`), `tests/should-respond.test.ts` (23, ported case-by-case from `test_should_respond.py`), `tests/delegate.test.ts` (8), `tests/room-query.test.ts` (14, covers solo/completed/timeout + `#54` offline annotation + ghost `[ROOM_QUERY]` filter), `tests/claude-code.test.ts` (10, hermetic — SDK stubbed via `sdkLoader`), `tests/cli.test.ts` (6), plus smoke (2). Total: 119.
  - Python: `packages/machine/tests/test_spawner.py` gains 4 new cases — TS path hit, TS npx fallback, `agent_binary_resolved` log shape with `runtime="typescript"`, and a regression guard that default (`runtime="python"`) doesn't even probe for the TS binary. `packages/cluster/tests/test_agents_api.py` gains 2 new cases (runtime default + TS create + update). `tests/test_migrations.py` head pointer bumped from 015 → 016 (4 assertions).

## Result

- `npm run test:ts` — **119 tests pass** across 9 files (vitest).
- `packages/cluster && uv run pytest tests/` — **368 pass** (incl. new runtime cases; migration tests happy at 016 head).
- `packages/machine && uv run pytest tests/` — **213 pass** (incl. 4 new TS-branching cases).
- `packages/agent && uv run pytest tests/` — **132 pass** (unchanged, zero regression on the Python runtime).
- `alembic upgrade head` + `alembic downgrade 015` → `upgrade head` round-trips cleanly on SQLite; `agents.runtime` column appears and disappears as expected.
- `cd packages/agent-ts && npm run build` → `dist/cli.js` 36.8 KB ESM bundle with sourcemap + `.d.ts`. `node dist/cli.js --help` prints the expected flags.
- `npm run lint:ts` — 0 errors / 0 warnings.
- `uv run ruff check` on all files touched by this commit — 0 new errors (pre-existing ruff findings in unrelated modules left alone).
- Python and TS runtimes can now coexist in the same cluster; admins select per-agent via the `runtime` field. The Python arm is untouched, so existing agents keep running on `doorae-agent` with no action needed.
- SDK preview caveat: the Claude Code SDK V2 API is `@alpha`; `package.json` pins `0.2.110` exactly and the README documents the manual-smoke-test requirement on upgrade.
- Deferred to follow-up issues: Codex/Gemini adapters (Phase 2), frontend runtime selector UI, automated live E2E, shared `packages/protocol/` schema.
