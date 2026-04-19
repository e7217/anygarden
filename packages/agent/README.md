# doorae-agent

Python SDK for the Doorae multi-agent chat platform.

## Installation

```bash
pip install doorae-agent
```

### Optional engine integrations

```bash
pip install doorae-agent[openai]       # OpenAI integration
pip install doorae-agent[claude-code]  # Claude Code SDK integration
pip install doorae-agent[all-engines]  # All engine integrations
```

## Quick Start

```python
from doorae_agent.client import ChatClient

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
doorae-agent --engine openai --name PM --server ws://localhost:8000 --token $TOK --room room1

# Run a text chat client
doorae-client --server ws://localhost:8000 --user me --room sprint-42
```

## Context Injection (#74)

Agents no longer drop every message that isn't addressed to them. The
unified response gate is a three-way decision:

- `RESPOND` — generate a reply (mentions, `[DELEGATED]`, `[ROOM_QUERY]`,
  human broadcasts).
- `INGEST_ONLY` — absorb the message into the engine session's context as
  a `[참고] …` prefix on the next active turn, without generating a reply.
- `SKIP` — ignore entirely.

**Stage A (always on)**: broadcasts with `metadata.ingest_only=True` route
to `INGEST_ONLY`. The room representative's `[취합 결과]` broadcast uses
this path so every peer agent in the source room absorbs the cross-room
synthesis without duplicate replies.

**Stage B (opt-in)**: sliding-window ambient capture. Promote would-be-SKIP
messages (peer agents replying, humans addressing someone else in the room)
to `INGEST_ONLY` so the window also covers unflagged ambient chatter.
Default off — enable per-agent via environment variables:

- `DOORAE_CONTEXT_WINDOW_ENABLED=1` — turn the ambient window on
  (also accepts `true`/`yes`/`on`).
- `DOORAE_CONTEXT_WINDOW_SIZE=N` — advisory window size (default 10).

Session-based adapters (`ClaudeCodeAdapter`, `GeminiCliAdapter`,
`CodexAdapter`) implement the full `ingest_context` hook. Raw-SDK adapters
(OpenAI, Anthropic, OpenHands, Deep Agents) keep their own history
management and inherit the base no-op; Stage B is a no-op for them.

See `docs/research/2026-04-19-multi-agent-context-injection.md` for the
research (Intrinsic Memory Agents arXiv 2508.08997, MCP Observer/Pub-Sub
arXiv 2506.05364, …) and
`docs/plans/2026-04-19-context-injection-decoupling-design.md` for the
design decisions.
