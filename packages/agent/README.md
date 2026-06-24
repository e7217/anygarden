# dragent

Python SDK for the Anygarden multi-agent chat platform.

## Installation

```bash
pip install dragent
```

### Optional engine integrations

```bash
pip install dragent[openai]       # OpenAI integration
pip install dragent[claude-code]  # Claude Code SDK integration
pip install dragent[all-engines]  # All engine integrations
```

## Quick Start

```python
from anygarden_agent.client import ChatClient

client = ChatClient("ws://localhost:8000", token="your-token", agent_name="MyAgent")

@client.on_message
async def handle(msg):
    print(f"[{msg['participant_id']}] {msg['content']}")

await client.join_room("room-id")
await client.run()
```

## CLI Usage

```bash
# Run an agent
anygarden-agent --engine openai --name PM --server ws://localhost:8000 --token $TOK --room room1

# Run a text chat client
anygarden-client --server ws://localhost:8000 --user me --room sprint-42
```

## Context Injection (#74)

Agents no longer drop every message that isn't addressed to them. The
unified response gate is a three-way decision:

- `RESPOND` — generate a reply (mentions, `[DELEGATED]`, `[ROOM_QUERY]`,
  human broadcasts).
- `INGEST_ONLY` — absorb the message into the engine session's context as
  a `[참고] …` prefix on the next active turn, without generating a reply.
- `SKIP` — ignore entirely.

**Server-driven stamping (#74 Stage A + #148)**: broadcasts with
`metadata.ingest_only=True` route to `INGEST_ONLY`. Producers are:

- The room representative's `[취합 결과]` broadcast (cross-room synthesis).
- The cluster itself for ambient messages in rooms where
  `context_window_enabled=True` (#148 Part 3). Admins toggle the flag per
  room from the Edit room dialog.

Agents can opt out per-agent via the `agents.context_window_opt_out` flag
(surfaced as "대화 맥락 공유 제외" in `AgentSettingsMenu`); opted-out agents
turn a received `ingest_only` broadcast into `SKIP` in `decide_policy`.

**Deprecated**: the former `ANYGARDEN_CONTEXT_WINDOW_ENABLED` /
`ANYGARDEN_CONTEXT_WINDOW_SIZE` environment variables from Stage B (#74 Part
B) are removed as of #148 Part 4. The decision now lives in the cluster DB
and takes effect the next time the agent reconnects (Part 2's UI toggle
triggers a `bump_generation` respawn so the refresh is automatic).

Session-based adapters (`ClaudeCodeAdapter`, `GeminiCliAdapter`,
`CodexCliAdapter`) implement the full `ingest_context` hook. Raw-SDK adapters
(OpenAI, Anthropic, OpenHands, Deep Agents) keep their own history
management and inherit the base no-op.

See `docs/research/2026-04-19-multi-agent-context-injection.md` for the
research (Intrinsic Memory Agents arXiv 2508.08997, MCP Observer/Pub-Sub
arXiv 2506.05364, …) and
`docs/plans/2026-04-19-context-injection-decoupling-design.md` for the
design decisions.
