# feat(chat): show what an agent is doing and keep a work trace on its reply (#880)

- Commit: `b8d1c59` (b8d1c59671fb6bec90d4fdac8dc97b8d7d75dd70)
- Author: Changyong Um
- Date: 2026-10-19T10:17:35+09:00
- PR: —

## Situation

#701 gave the waiting indicator three coarse stages (`preparing / using_tool / writing`). A shell command, a file edit and an MCP call all read "using a tool…", and Pi agents never showed even that because `pi.py` did not forward `tool_execution_*` events. The sentences an agent writes between tool calls ("There are 4 new commits. Same procedure as before.") were lost: Codex answers with `-o` (last message only), so they were dropped, and Pi joined every text into the answer, so they arrived late and mixed with the conclusion. Users could not tell what an agent was doing or had done.

## Task

- Show the running work kind with an ordinal and the latest narration while an agent works (Codex and Pi).
- Keep each narration and the work after it on the reply as a collapsed trace; the reply body holds only the final answer.
- Keep #701's rule: no commands, paths, arguments, tool names, tool output or reasoning on the wire or in the DB.
- Stay compatible with servers, agents and browsers that predate the change (federation peers, machine links).
- Only agents may send the detail; peers reading the room must not see the trace.

## Action

- `packages/cluster/anygarden/agent/runtime/execution/progress_stage.py` — `ActivityKind`, `activity_for_progress`, `narration_for_progress`, `TraceBuilder` (steps of narration + completed work, 50 steps / 16 KB, drops the earliest, drops a narration identical to the final answer).
- `.../execution/codex.py` — emits normalized `activity` (started/completed, file-change counts, failed patches not counted, `anygarden` MCP server → `anygarden_tool`) and `narration` events; a message becomes narration once more work or another message follows it.
- `.../execution/pi.py` — maps Pi tool names to kinds inside the runtime (`bash`→command, `edit/write`→file_change, `read/grep/find/ls`→file_read, self tools→anygarden_tool), thinking start/end, splits text: text in a message with a `toolCall` is narration, text after the last tool work is the reply, falling back to the last narration.
- `.../integrations/room_execution.py` — `_Progress` dataclass replaces the `(execution_id, stage)` tuple; `_observe_progress` feeds the trace and the heartbeat; trace attached to `EngineTurn.execution_trace` on success, failure and cancel.
- `agent/client.py`, `agent/protocol/frames.py`, `ws/protocol.py` — optional `activity`, `activity_seq` (1..10000), `narration` (≤280) on typing frames; client drops invalid values.
- `agent/runtime/handler_wrapper.py` — supervisor merges `execution_trace` into the reply and failure/timeout notices.
- `messages/metadata.py` `sanitize_execution_trace`, applied in `ws/handler.py` (agents normalized, others stripped) and `messages/router.py` (always stripped); typing detail relayed only for agents.
- Frontend: `lib/typingStage.ts` (`workDetailFrom`, `workLabel`), `lib/executionTrace.ts`, `hooks/useWebSocket.ts` (`typingWork`), `ChatArea.tsx` (narration beside the spinner), `ThreadPanel.tsx`, `ChatPage.tsx`, `GuestRoomPage.tsx`, new `components/ExecutionTrace.tsx` in `MessageBubble.tsx`, en/ko catalog strings.
- Docs: `docs/requirements.md` ENG-10..12, `docs/design/01-architecture.md` WS frame table, `CHANGELOG.md`.

## Decisions

- **Narration as separate room messages vs. live typing + trace on the single reply** — separate messages would wake peers through the mention-only policy (#739), could be taken as turn completion (#720), add notifications and pollute missed-conversation context (#866). Keeping one message per turn leaves all of that untouched. Chosen: typing preview + `execution_trace` metadata.
- **New `stage` values vs. separate optional fields** — `TypingFrame.stage` is a `Literal`, so an older server would reject the whole frame; `TypingFrame` has no `extra="forbid"`, so extra fields are silently ignored and the old behaviour remains. Chosen: separate fields; pinned by a compat test.
- **Who counts** — the server sees typing only every 2 s and cannot count short commands; the agent sees every event and already authors the reply body. Chosen: agent counts, server enforces the closed shape and caps.
- **Where to split narration from the answer** — the runtime is the only place that knows event order and makes `RuntimeResult.text` the answer, keeping receipts and retries consistent; string-subtracting in the adapter was fragile.
- **Tool names** — external MCP tool names are an open set; only closed kinds are sent. Allowlisted Anygarden tool names are a possible follow-up.
- Codex `reasoning` arrives only as `item.completed`, so Codex does not report `thinking` live; no prompt change was added because live runs showed Codex narrates without being asked.
- Revisit if: Pi changes event names (re-check with `PI_SAFE_EVENTS_REVIEWED_FOR`), a typing frame model gains `extra="forbid"`, or Codex stops writing `-o`.

## Result

- Waiting indicator reads e.g. `planner · running a shell command (#3)…` with the latest narration beside the spinner; Pi agents now report tool use.
- Replies after tool work carry a collapsed "Work" trace; Pi replies hold only the final answer (Codex replies unchanged).
- Tests: agent 820+ pass, cluster 3934 pass, vitest 973 pass, Playwright 62 existing + new `agent-work.spec.ts` (light/dark × 375/1440, repeated) pass; ENG-10..12 reported as pass by `scripts/req_report.py`.
- Pending: Pi verified only against fixtures from pi-agent-core 0.85.1 types (no local Pi credentials); live Pi and live federation runs not yet observed.
