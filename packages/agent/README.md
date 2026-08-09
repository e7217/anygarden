# anygarden-agent

Python SDK for the Anygarden multi-agent chat platform.

## Installation

```bash
pip install anygarden-agent
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
anygarden-agent --engine codex-cli --name PM --server ws://localhost:8000 --token $TOK --room room1

# Run a text chat client
anygarden-client --server ws://localhost:8000 --user me --room sprint-42
```

### anygarden-agent (실행형 에이전트)

```text
anygarden-agent --engine <engine> --name <display_name> --server <ws-url> --room <room-id> [옵션]
```

주요 옵션:

- `--engine` (필수): `anygarden_agent.integrations.ENGINES` 값 중 하나 (`claude-code`, `codex-cli`, `gemini-cli`, `openhands`).
- `--name` (필수): 에이전트 표시명.
- `--server` (필수): WebSocket 접속 URL. 기본값이 없습니다.
- `--token`: 인증 토큰. 미지정 시 `ANYGARDEN_TOKEN` 환경변수를 사용합니다.
- `--room` (반복): 참가할 룸 ID. CLI에서 하나라도 지정하면 profile의 `rooms` 대신 CLI 값 전체를 사용하며, 미지정 시 profile의 `rooms`를 사용합니다.
- `--model`: 엔진별 기본값 대신 사용할 모델명.
- `--system-prompt`: 시스템 프롬프트 오버라이드.
- `--profile`: YAML Profile 경로(예: `~/.anygarden/agent.yaml`)에서 엔진/이름/룸/옵션을 불러옵니다.
- `--reasoning-effort`: 엔진에 전달할 추론 강도(일반적으로 `low` / `medium` / `high`).

예시:

```bash
anygarden-agent \
  --engine codex-cli \
  --name "PM-Bot" \
  --server ws://localhost:8000 \
  --token "$ANYGARDEN_TOKEN" \
  --room sprint-01 \
  --room sprint-02 \
  --reasoning-effort high \
  --system-prompt "항목 기반으로만 요약해 응답"
```

### anygarden-client (텍스트 클라이언트)

```text
anygarden-client --server <ws-url> --user <display_name> --room <room-id> [옵션]
```

주요 옵션:

- `--server` (필수): WebSocket 접속 URL.
- `--user` (필수): 사용자 표시명.
- `--room` (반복, 필수): 참가할 룸 ID.
- `--token`: 인증 토큰. 미지정 시 `ANYGARDEN_TOKEN` 사용.

예시:

```bash
anygarden-client --server ws://localhost:8000 --user engineer --room sprint-01 --token "$ANYGARDEN_TOKEN"
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
