# feat(agent): decouple context ingestion from response gate (#74)

- Commit: `d0acb3c` (d0acb3c0dfbea5343015ca32ce64f909803e813a)
- Author: Changyong Um
- Date: 2026-04-19T09:19:31+09:00
- PR: #74 (Issue, PR TBD)

## Situation

Agents in a Doorae room only saw messages that passed the unified `should_respond` boolean gate — mentions, `[DELEGATED]` / `[ROOM_QUERY]` prefixes, and 1:1 direct messages. Everything else was silently dropped at the adapter before ever reaching the engine SDK session. The most visible symptom was the room-representative `[취합 결과]` broadcast: after a `<#room>` cross-room query, the synthesis landed in the source-room UI but never in the LLM context of the other agents in that room. When the user followed up asking one of those agents to reason about the synthesis, the agent had no idea what had been reported.

Deep-research into 18 MAS sources (see `docs/research/2026-04-19-multi-agent-context-injection.md`) confirmed this was a structural gap: the industry standard is "shared history + speaker selection" (AutoGen, LangGraph, CAMEL, AgentVerse), while Doorae delegates session management to each engine SDK's `resume`. The bool-only `should_respond` conflates "who should reply" with "who should update their context" — these are independent decisions in the literature (Intrinsic Memory Agents arXiv 2508.08997; MCP Observer/Pub-Sub patterns arXiv 2506.05364).

## Task

Implement Stage A of the 2-stage plan in `.tmp/plan-74-context-injection-unified.md`:

- Promote `should_respond` to a three-way decision (`RESPOND` / `INGEST_ONLY` / `SKIP`) without breaking the 33 existing call sites.
- Add an `EngineAdapter.ingest_context` hook that absorbs a message into engine context without triggering a reply.
- Wire the `[취합 결과]` broadcast to flag itself for ingest-only absorption so peer agents pick it up.
- Ship ClaudeCodeAdapter as the first session-based implementation; leave Gemini / Codex on the default no-op until Stage B.
- Do not touch cluster or agent-ts — server stays a pass-through; TS port is tracked in #73.
- Preserve all existing behavior: no regression on any `test_should_respond` case, no change in delegate / room_query / LLM flow for messages that still end up `RESPOND`.

## Action

- `packages/agent/doorae_agent/integrations/base.py`: introduced `MessagePolicy` enum and `decide_policy` (6 rules + new ingest_only placement between rule 3 and rule 5, so direct mentions still win). Rewrote `should_respond` as a one-line wrapper around `decide_policy(...) == RESPOND` — every existing caller (claude_code:248, codex:161, gemini_cli:289, 23 tests) keeps working. Added `EngineAdapter.ingest_context` as a default no-op so untouched adapters stay source-compatible.

- `packages/agent/doorae_agent/integrations/claude_code.py`: added `_PENDING_CONTEXT_MAX=10` / `_PENDING_CONTEXT_TTL_SEC=600` module constants, a `_pending_context: dict[str, list[tuple[float, str]]]` per-room buffer, and three methods: `ingest_context`, `_format_context_line`, `_drain_pending_context`. `on_message` now drains the buffer into a `[참고] …\n\n{content}` prefix before handing the prompt to the SDK. `integrate_with_claude_code._handle` replaced its `if not should_respond(msg, client): return` with a three-way match on `decide_policy`, routing `INGEST_ONLY` to `adapter.ingest_context` and returning without hitting the typing indicator or SDK.

- `packages/agent/doorae_agent/integrations/room_query.py`: `_deliver_result` now adds `"ingest_only": True` to its broadcast metadata alongside `room_query_result`. Single-line change at the `client.send(...)` call site. Server (cluster) broadcast path passes metadata through unchanged, so this requires zero cluster-side work.

- Tests:
  - `test_should_respond.py`: 10 new cases covering the `ingest_only` flag (alone, with mention, with DELEGATED, with self-message, with room_query rep gate) and verifying `should_respond` still maps enum→bool correctly. Added `TestIngestContextDefault` to pin the base no-op.
  - `test_claude_code.py`: 7 new cases under `TestIngestContext` — buffer lifecycle, single consumption, room isolation, empty-content skip, FIFO size cap, room_query_result locator formatting, and a full handler-level end-to-end showing one `ingest_only` broadcast causing zero LLM calls but surfacing as `[참고]` prefix on the next human turn.
  - `test_room_query.py`: added `assert kwargs["metadata"]["ingest_only"] is True` to all three `_deliver_result` call-site tests (solo / completed / timeout).

- `docs/plans/2026-04-19-context-injection-decoupling-design.md`: flipped status from `proposed` to `implemented-stage-a` and corrected the flag-attachment location note (agent-side, not server).

- `docs/research/2026-04-19-multi-agent-context-injection.md` + sources/evidence jsonl: included in this PR so reviewers can trace the design decisions to primary literature.

## Decisions

**Gate shape — 3-state enum vs two booleans vs inline flags.** The plan (`.tmp/plan-74-context-injection-unified.md` §6.2 decision 5) weighed (a) introducing `MessagePolicy` now and wrapping `should_respond`, (b) keeping bool but adding a sibling `should_add_to_context` helper, (c) leaving the gate alone and gating ingestion inside each adapter. We picked (a) because every adapter would have needed the same `if ingest ... elif respond ...` branch anyway once Stage B lands, and the wrapper covers all nine existing `should_respond` callers without touching them. Rejected (b): a sibling bool can't express "RESPOND wins over INGEST_ONLY" without extra rules, and the cross-branch ordering would drift as call sites copy-paste. Rejected (c): duplicates gate logic across seven adapters and makes testing the handoff painful.

**Flag placement in `decide_policy`.** First draft put the `ingest_only` check right after rule 2b (room_query rep gate), which caused `test_direct_mention_beats_ingest_only` to fail: a broadcast that also happened to mention someone was being absorbed as context instead of replied to. Moved the check to sit between rule 3 (mentioned_me → RESPOND) and rule 5 (someone-else mentioned → SKIP). Tipping observation: addressability is a stronger signal than the flag — if the server bothered to resolve a mention to *you*, the message is actionable work, not background information. The flag still promotes the rule 4/6 "would-be-SKIP" cases to INGEST_ONLY, which is exactly what `[취합 결과]` needs.

**Buffer location — adapter instance vs accumulator singleton.** Stage A's Intrinsic Memory Agents reference (arXiv 2508.08997) argues for per-agent isolation. Singleton buffers break that cleanly in multi-adapter tests and would have forced fixture resets in every test. Going with an instance field also makes Stage B a drop-in: `ContextAccumulator` will own the *policy* of when to capture ambient, but storage stays in `_pending_context`.

**Injection shape — prompt prefix vs system_prompt vs tool result.** Claude Agent SDK's `resume` session accumulates prompt content as user turns. Prefixing `[참고] …` before the current content relies on in-context learning to distinguish breadcrumb from new question. Considered routing through `system_prompt` but the SDK's append-vs-replace semantics aren't guaranteed (one of the explicit risks in design doc §6 risk 1). Considered tool-result shape — distorts the tool protocol for a non-tool event. Prefix won on simplicity + SDK-neutrality; risk is flagged for post-deployment validation (see design doc §6).

**Flag attached by agent vs cluster.** Cluster's `ws/handler.py` is supposed to be a pass-through broadcaster; teaching it to recognize `room_query_result` would cross the service boundary. The single-line change in `room_query._deliver_result` is local to the code that already constructs this metadata. Rejected dual-attachment for drift risk.

**Assumptions that could trigger revisiting**: (i) that `[참고]` labels are actually interpreted as external breadcrumbs by Claude Opus/Sonnet — flagged in design doc §6 risk 1, to be smoke-tested manually in the follow-up, (ii) that TTL+size-cap are a sufficient replacement for Stage B's sliding window for the `[취합 결과]` case — plan §4 explicitly promises to re-evaluate after two weeks of observation, (iii) that ambient-message absorption isn't needed this sprint — if multi-party rooms show the "other agent replied, this agent can't reason about it" gap often enough, Stage B lands next.

## Result

`uv run pytest packages/agent/tests/` shows 150 passed / 1 pre-existing env-only failure (`test_openai` without `OPENAI_API_KEY`). `test_should_respond.py` is 33 PASS (23 baseline + 10 new), `test_claude_code.py` is 15 PASS (8 baseline + 7 new), `test_room_query.py` is 20 PASS with three reinforced assertions. Ruff on the changed files is clean aside from two pre-existing issues in `test_room_query.py`'s unrelated imports.

Behavioral outcome: the `[취합 결과]` synthesis broadcast now carries `ingest_only=True`; each peer ClaudeCodeAdapter in the source room stashes the formatted `[참고] 룸 <target>에서 다음 응답이 왔습니다: …` line into its per-room buffer without taking a turn. The next time that agent is addressed by a human, its prompt is prefixed with the breadcrumb, so follow-up reasoning like "based on what the other room said, …" now has grounding. Zero fan-out: one broadcast produces N context updates, not N replies.

Pending for the follow-up PR: Stage B sliding-window accumulator for unflagged ambient traffic, Gemini / Codex `ingest_context` implementations, TS agent port (#73), and the manual smoke test in a live `make dev` environment (design doc §6 risk 1 validation).
