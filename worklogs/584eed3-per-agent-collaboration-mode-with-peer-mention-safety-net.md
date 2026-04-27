# feat(agents): per-agent collaboration_mode with peer-mention safety net (#279)

- Commit: `584eed3` (584eed3ba2bf9f9f4d626f85350a589887ecc190)
- Author: Changyong Um
- Date: 2026-04-27
- PR: #279

## Situation

Admins repeatedly hit a structural gap in the orchestrator stack: room-scoped speaker strategy (`mentioned_only` / `round_robin` / `orchestrator`) only picked *who* spoke, never *how* they answered. The `agent01` orchestrator in test03 quietly self-answered the user's "출장 준비" question (seq 17) instead of delegating to codex/gemini/claude2, even though the handoff machinery itself was healthy. `agents_md` is an agent-scoped prompt body, so trying to encode "always delegate" inside it leaks the policy to every other room the agent participates in. After deep-research and a brainstorm, the agreed direction was a per-agent collaboration policy rather than a new room column — same axis OpenAI Agents SDK and AutoGen `SelectorGroupChat` have settled on — combined with a server-side safety net so the new prompt path can't loop the cluster.

## Task

- Add an `agents.collaboration_mode` enum (`solo` | `collaborative`, default `solo`) plumbed end-to-end: model + migration + REST PATCH/GET + welcome frame + agent SDK cache + admin UI toggle.
- Stop duplicating the participants-roster suffix logic across adapters: lift it to `ChatClient.compose_roster_suffix(..., with_collaborative_hint=...)` so claude_code, codex, and gemini_cli all consume the same composer with one branch deciding when to attach.
- Stand up a peer-mention safety net before opening the door to "agent delegates by mentioning another agent": stamp `metadata.peer_depth` + `metadata.kind` on every peer-target broadcast and bound the chain via `PeerHandoffBudget` (max depth 1, max 8 total per user turn).
- Cover the new surface with unit tests for the helpers, regression tests for the existing toggle pattern, and integration tests through the WS handler.

## Action

- **Schema (#1 / #3)** — alembic migration `034_agent_collaboration_mode.py` adds `agents.collaboration_mode VARCHAR(16) NOT NULL DEFAULT 'solo'`; `Agent` SQLAlchemy model surfaces it as a `Mapped[str]`. Migration round-trip verified manually (`alembic upgrade head` → `downgrade -1` → `upgrade head`).
- **REST API (#2)** — `AgentCreate` / `AgentUpdate` / `AgentOut` pick up the new field with the established `*_set` opt-in idiom (mirrors `context_window_opt_out` and `description`). `Field(pattern="^(solo|collaborative)$")` rejects garbage at the schema layer; the PATCH handler treats the change as peer metadata (no `bump_generation` — the new value is consumed by the SDK on the next welcome, not by the agent subprocess).
- **Welcome frame (#3)** — `WelcomeOut.my_collaboration_mode` defaults to `"solo"` so user/guest welcomes stay byte-identical; `ws/handler.py` reads the agent's stored value in the same DB round-trip as `context_window_opt_out` to avoid a second hop.
- **Agent SDK (#4)** — `ChatClient` gains `_collaboration_mode_by_room`, `is_collaborative(room_id)`, and `compose_roster_suffix(room_id, *, with_collaborative_hint=False)`. The hint paragraph instructs the agent to use `<@user:UUID>` mentions and synthesize peer replies, but is omitted for solo agents. The three adapters now call the helper:
  - `claude_code.py`: hoists the roster attach branch out of the orchestrator-only `if`, so a collaborative non-orchestrator also receives the suffix.
  - `codex.py`: tracks a sha for `_roster_injected` (parallel to `_memory_injected`) since codex threads accumulate prefix injections; updates trigger a `[팀 구성 업데이트]` header.
  - `gemini_cli.py`: appends to the per-call prompt builder; gemini's CLI is stateless so no sha tracking needed.
- **Safety net (#5)** — `orchestration/rules.py` grows `MAX_PEER_DEPTH`, `MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN`, `is_peer_mention`, `compute_outbound_peer_depth`, `strip_peer_mentions_from_content`, and `PeerHandoffBudget`. `ws/handler.py` wires them inline with the existing mentions-on-send block: agent senders trigger consume + stamp; human/guest senders reset the per-room budget. When the cap trips, the offending peer mention tokens are removed from the broadcast content (regex collapse keeps the prose readable) and `metadata.peer_blocked=True` plus `peer_depth` are stamped for observability.
- **Frontend (#6)** — `OverviewPanel.tsx` adds a Collaboration row in the metadata grid with a `solo` / `collaborative` `<select>` and a one-line helper that warns about >2 collaborative agents per room. `useAgents.ts` extends the `Agent` interface and the `updateAgent` patch type. `npm run build` passes (vite + tsc); 347 frontend unit tests stay green.
- **Tests (#7)** — 11 new unit tests for the rules helpers (`PeerMentionSafetyNet` class), 2 new API toggle tests (`test_update_agent_collaboration_mode_toggle`, `test_create_agent_with_collaboration_mode`), 3 new claude_code prompt-shape tests (`test_collaborative_non_orchestrator_gets_roster_with_hint`, `test_solo_non_orchestrator_prompt_unchanged`, `test_orchestrator_collaborative_combination_attaches_hint`), and 5 integration tests against the live WS handler in `test_collaboration_mode.py` (welcome stamping for solo + collaborative, peer_depth/kind on first peer-ask, strip on second peer-ask in same turn, budget reset on human send).

## Result

811/811 cluster tests pass (`pytest --ignore=tests/test_e2e_materialize.py`), 288/288 agent tests pass (`--ignore=tests/test_integrations/test_openai.py` — the openai key is unset on this machine, same skip as on `main`), 347/347 frontend tests pass, `npm run build` is clean. Lint diff vs `main` shows zero new errors — `ruff check packages/` returns the same 129 pre-existing warnings (line numbers shifted in two files because we added code there). The whole change lands as one PR-ready commit covering schema → backend → SDK → frontend → tests, plus updated CHANGELOG entries on both `cluster` and `agent` packages.

Behavioural impact:
- Existing `solo` agents (the entire fleet pre-#279) emit byte-identical LLM prompts; opt-in only.
- Setting an agent to `collaborative` in the admin UI flips the toggle without a respawn — the next welcome (reconnect or the next message turn for stateless gemini) picks up the new mode.
- An agent that peer-mentions another agent gets `peer_depth=1` and `kind=peer_query` on the broadcast; the peer's reply that itself contains another peer mention is depth-2 and the mention is stripped before broadcast, breaking the loop while the prose answer still reaches the user. A new human/guest message resets the budget so the next turn starts fresh.
- The plan deliberately punted `room.collab_override`, free-text addons, `fan_out` strategy, and embedding-based expertise routing to follow-up issues; the agent-axis-only design proved sufficient for every case captured in the brainstorm.
