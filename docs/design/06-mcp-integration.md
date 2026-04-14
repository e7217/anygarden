# 06. MCP 통합 — 외부 도구 영역 (채팅 프로토콜과 완전 분리)

> **한 줄 요약**: 채팅 서버는 MCP를 **모른다**. 각 에이전트 엔진이 자체 MCP 클라이언트로 외부 도구(GitHub, Jira, Filesystem 등)를 호출하고, 결과를 일반 채팅 메시지로 게시할 뿐이다. 서버의 역할은 `metadata`에 출처 태그를 저장하는 것뿐이다.

이 문서는 이 구현에서 **MCP를 채팅 프로토콜로 쓰지 않는 이유**와 **그럼에도 에이전트 엔진이 MCP를 자유롭게 사용할 수 있는 방법**을 정리한다.

---

## 6.1 MCP는 무엇이고 무엇이 아닌가

### MCP의 본래 목적

**MCP (Model Context Protocol)** 는 LLM 에이전트가 외부 도구·리소스·프롬프트를 표준화된 방식으로 호출하기 위한 프로토콜이다. 주 사용처:

- GitHub 이슈 조회 · PR 생성
- 파일 시스템 읽기/쓰기
- 데이터베이스 쿼리
- Jira 티켓 생성
- Slack 메시지 전송
- 사용자 지정 내부 API

### MCP가 아닌 것

- **채팅 프로토콜이 아니다**. MCP는 "에이전트 ↔ 도구" 경로이지 "에이전트 ↔ 에이전트" 경로가 아니다.
- **메시지 브로커가 아니다**. 여러 에이전트가 같은 채널에 메시지를 뿌리는 용도로 설계되지 않았다.
- **양방향 비동기 push를 지원하지 않는다**. 기본 구조는 request/response이다.

---

## 6.2 "채팅 서버를 MCP 도구로 래핑"하는 접근의 문제

초기 설계 탐색에서 다음과 같은 유혹이 있었다:

> "채팅 서버를 MCP 서버로 만들고, `send_message` / `get_messages` 같은 tool을 노출하자. 그러면 모든 에이전트 엔진이 MCP 클라이언트로 서버에 접속할 수 있다."

이 접근의 **근본적 문제**:

### 문제 1: 토큰 효율 10-30배 손실

MCP의 `tool_call` + `tool_result` 구조는 JSON 메타데이터가 많다. 한 메시지당:

```
# MCP 경로 (한 메시지 왕복)
LLM → "{type: 'tool_use', name: 'send_message', input: {...}}"   ~300 tokens
Tool → "{type: 'tool_result', content: 'ok, message_id: ...'}"    ~200 tokens

# 수신 측
LLM → "{type: 'tool_use', name: 'get_messages', input: {...}}"    ~250 tokens
Tool → "{type: 'tool_result', content: [{...}, {...}, {...}]}"    ~800 tokens (N개 메시지)

합계: ~1,500 tokens/메시지 1건 왕복
```

```
# WebSocket 네이티브 경로 (한 메시지 왕복)
LLM → "Hero 시안 어떻습니까?" (일반 assistant response)            ~20 tokens
SDK가 WebSocket 프레임으로 변환 (LLM 토큰 소모 없음)
상대 LLM의 다음 턴에서 "[PM] Hero 시안 어떻습니까?" 로 주입         ~30 tokens

합계: ~50 tokens/메시지 1건 왕복
```

**30배 차이**. 하루 1,000 메시지 × 4 에이전트 × 30일 = 월 100만 건 수준에서 이 차이는 LLM 비용 $4,000 vs $100,000이 될 수 있다.

### 문제 2: 대화 턴이 아니라 도구 결과로 주입됨

LLM이 MCP 경로로 메시지를 받으면, 이것은 "도구가 반환한 값"으로 대화 이력에 들어간다. 그러면 LLM은:

- 다른 참여자의 발언을 **외부 도구의 출력**으로 인식한다
- 대화의 흐름/감정/맥락을 구성하지 못한다
- `"@PM"` 같은 멘션을 놓치기 쉽다 (도구 결과 블록 안에 묻힘)

반면 네이티브 대화 턴으로 주입하면:

- 상대의 발언이 `user` role 메시지로 들어간다
- LLM의 학습 데이터와 정확히 일치하는 구조 (사람과의 대화와 동일)
- 멘션·뉘앙스·암묵적 맥락을 자연스럽게 이해

### 문제 3: 서버가 MCP 프로토콜 전체를 구현해야 함

MCP는 도구 호출 외에 **리소스**(파일 같은 정적 콘텐츠), **프롬프트**(재사용 템플릿), **sampling**(LLM이 MCP 서버에게 LLM 호출을 부탁) 등의 기능이 있다. 채팅 서버가 MCP 서버가 되려면 이 모두를 구현해야 하거나, 부분 구현의 트레이드오프를 감수해야 한다.

**결론**: MCP는 훌륭한 도구 프로토콜이지만, 채팅용으로 쓰면 안 된다. EP00과 Plan A가 일관되게 주장하는 이 경계는 이 구현에서도 유지된다.

---

## 6.3 그럼 에이전트는 어떻게 MCP를 쓰는가

**에이전트 엔진이 자체적으로 쓴다**. 채팅 서버는 전혀 관여하지 않는다.

### 6.3.1 Claude Code SDK + MCP

```python
from claude_agent_sdk import Agent
from doorae_sdk import ChatClient

agent = Agent(
    model="claude-sonnet-4-6",
    instructions="너는 PM이다...",
    # ↓ 이 부분은 채팅 서버와 완전히 무관하다
    mcp_servers=[
        {"name": "github", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
        {"name": "filesystem", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/me/workspace"]},
    ],
)

# 채팅 서버 연결 (MCP와 독립)
client = ChatClient(url="wss://doorae.example.com/ws", token="agt_...")
await client.connect()
await client.join_room("room_sprint_42")
client.integrate_with_claude_code(agent)

await client.run_forever()
```

**관찰**:
- `mcp_servers=[...]` 는 Claude Code SDK의 설정이다. Doorae 서버는 이 설정의 존재조차 모른다.
- PM 에이전트가 GitHub MCP로 이슈를 조회해도, 그것은 PM의 LLM 컨텍스트 안에서 일어나는 일이다.
- 조회 결과를 채팅방에 공유하고 싶으면, PM의 LLM이 "GitHub 이슈 #142 확인 완료"라는 일반 발언을 생성한다. 이 발언이 WebSocket으로 서버에 전송된다.

### 6.3.2 Codex SDK + MCP

```python
from codex_sdk import Session

session = Session(
    profile="techlead",
    mcp_servers={
        "jira": {"url": "http://localhost:8001/mcp"},
        "github": {"url": "http://localhost:8002/mcp"},
    },
)
# 이하 Claude Code와 동일한 패턴
```

### 6.3.3 OpenHands + MCP

OpenHands는 자체적으로 Runtime 내부에서 도구를 관리한다. MCP는 선택적으로 추가 가능하다.

```python
from openhands import Runtime

runtime = Runtime(
    config={
        "mcp_servers": [
            {"name": "filesystem", "url": "stdio://fs-server"},
        ],
    },
)
```

### 6.3.4 Deep Agents + MCP

LangGraph 생태계는 `langchain-mcp-adapters`를 통해 MCP를 도구로 흡수한다.

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents import create_deep_agent
from doorae_sdk import ChatClient

# 채팅 서버 연결 (6.3.1 예시와 동일한 ChatClient 인스턴스)
client = ChatClient(url="wss://doorae.example.com/ws", token="agt_...")

mcp_client = MultiServerMCPClient({
    "github": {"command": "npx", "args": [...]},
})
tools = await mcp_client.get_tools()

graph = create_deep_agent(
    tools=tools,
    middleware=[client.as_deep_agents_middleware()],
)
```

**모든 경우에 공통**: MCP 설정은 에이전트 엔진의 영역이며, Doorae 서버는 이를 모른다. SDK도 MCP를 직접 다루지 않는다.

---

## 6.4 도구 결과를 채팅 메시지로 공유하는 방법

에이전트가 MCP 도구를 호출한 결과를 **채팅방에 공유**하고 싶을 때, 이것은 일반 `send_message` 프레임으로 전송된다. 단, 출처를 `metadata`에 태그할 수 있다.

### 예시: GitHub 이슈 조회 결과 공유

PM 에이전트 내부의 LLM 대화 (일부):

```
User (주입된 메시지): "[Host] 스프린트 42의 남은 이슈가 뭐가 있나요?"

Assistant (PM LLM이 결정):
  → tool_use: github.list_issues(milestone="sprint-42", state="open")

Tool result (MCP 경로, 서버 무관):
  → [{"number": 142, "title": "Hero 섹션 재디자인"},
     {"number": 145, "title": "결제 버그 수정"}]

Assistant (PM LLM이 자연어로 응답):
  → "현재 2개의 이슈가 남아 있습니다:
     #142 Hero 섹션 재디자인
     #145 결제 버그 수정
     우선순위를 정해야 할 것 같습니다."
```

이 응답이 SDK의 `AssistantResponseHook`에 잡히면, SDK는 WebSocket 프레임으로 변환한다:

```json
{
  "type": "send_message",
  "room_id": "room_sprint_42",
  "content": "현재 2개의 이슈가 남아 있습니다:\n#142 Hero 섹션 재디자인\n#145 결제 버그 수정\n우선순위를 정해야 할 것 같습니다.",
  "metadata": {
    "tool_source": "github",
    "tool_name": "list_issues",
    "tool_call_id": "toolu_01ABC...",
    "tool_args": {"milestone": "sprint-42", "state": "open"}
  }
}
```

**서버의 역할**:

```python
# doorae/messages/service.py (발췌)
async def append_message(
    db: AsyncSession,
    *,
    room_id: UUID,
    participant_id: UUID,
    content: str,
    metadata: dict,  # ← 그대로 저장. 서버는 해석하지 않음
) -> Message:
    seq = await _next_seq(db, room_id)
    msg = Message(
        room_id=room_id,
        participant_id=participant_id,
        seq=seq,
        content=content,
        extra_metadata=metadata,  # JSON 컬럼에 그대로
    )
    db.add(msg)
    await db.commit()
    return msg
```

**서버는 `metadata`의 내용을 검증하지 않는다**. 저장하고, 브로드캐스트할 때 프레임에 포함시킨다. 수신 측 SDK 또는 UI가 이를 해석해 "이 메시지는 GitHub 도구 결과입니다" 같은 UI 힌트를 보여줄 수 있다.

---

## 6.5 `metadata` 표준 태그 (선택적 컨벤션)

서버는 `metadata`를 자유형 JSON으로 취급하지만, SDK 차원에서 **공통 컨벤션**을 권장한다.

| 필드 | 타입 | 의미 | 예 |
|---|---|---|---|
| `tool_source` | string | MCP 서버 이름 | `"github"`, `"filesystem"` |
| `tool_name` | string | 호출된 도구 이름 | `"list_issues"`, `"read_file"` |
| `tool_call_id` | string | LLM이 부여한 호출 ID | `"toolu_01ABC..."` |
| `tool_args` | object | 호출 인자 (민감 정보 제외) | `{"milestone": "sprint-42"}` |
| `mentions` | array[string] | 파싱된 멘션 이름 | `["PM", "TechLead"]` |
| `priority` | string | `"normal"` \| `"high"` | 멘션 시 자동 설정 |
| `reply_to` | string (UUID) | 응답 대상 메시지 ID | `"msg_01HX..."` |

이 컨벤션은 **강제되지 않는다**. SDK는 이를 자동으로 채워 넣고, UI는 이를 읽어 표시하면 된다. 새로운 필드를 추가해도 서버는 받아들인다.

---

## 6.6 시각적 경계

```
┌──────────────────────────────────────────┐
│ Machine A                        │
│                                          │
│  ┌────────────────────┐                  │
│  │ Claude Code SDK    │                  │
│  │                    │                  │
│  │ ┌────────────────┐ │    stdio / HTTP  │
│  │ │  PM LLM loop   │ │ ────────────────▶│── GitHub MCP server
│  │ │                │ │                  │
│  │ │ @tool: list_.. │ │ ◀────────────────│── (외부 프로세스)
│  │ └────────────────┘ │   tool result    │
│  │         ↓          │                  │
│  │  AssistantResponse │                  │
│  │       Hook         │                  │
│  └──────┬─────────────┘                  │
│         │                                │
│         ↓                                │
│  ┌────────────────────┐                  │
│  │  doorae-sdk        │                  │
│  │  (WebSocket)       │                  │
│  └──────┬─────────────┘                  │
└─────────┼────────────────────────────────┘
          │
          ↓ WebSocket (metadata 포함 일반 메시지)
┌──────────────────────────────────────────┐
│ doorae-server                            │
│                                          │
│  ┌──────────────────────────┐            │
│  │ /ws/rooms/{id}           │            │
│  │                          │            │
│  │  INSERT messages         │            │
│  │  broadcast to room       │            │
│  │                          │            │
│  │  [서버는 GitHub을 모름]  │            │
│  │  [metadata는 자유형 JSON]│            │
│  └──────────────────────────┘            │
└──────────────────────────────────────────┘
```

**수평 점선의 의미**: MCP 화살표(Claude Code SDK ↔ GitHub MCP server)는 **절대 서버 박스를 관통하지 않는다**. 만약 관통한다면, 그것은 이 구현의 원칙을 위반하는 설계다.

---

## 6.7 FAQ

### Q1: MCP 도구 호출 결과를 다른 에이전트가 직접 참조할 수 있나?

**A**: 직접 참조 불가. 다른 에이전트는 "채팅방에 공유된 일반 메시지"로만 볼 수 있다. 도구 결과가 필요하면 LLM이 자연어로 요약한 내용을 읽는다. 이것이 오히려 **안전한 경계**이다 — 한 에이전트의 내부 상태가 다른 에이전트에 직접 노출되지 않는다.

### Q2: 채팅방에 올라온 도구 결과를 인덱싱/검색할 수 있나?

**A**: 메시지 본문은 일반 텍스트이므로 `LIKE` 검색 가능. `metadata.tool_source` 필드로 "GitHub 결과만 보기" 같은 필터도 가능. 서버 REST API에 필터 파라미터를 추가하는 것은 선택 사항이다.

### Q3: MCP sampling 기능은 지원하나?

**A**: 서버 차원에서 지원하지 않음. MCP 서버가 에이전트의 LLM을 호출하는 sampling 기능은 에이전트 엔진 내부에서만 동작한다. 채팅 서버는 관여하지 않는다.

### Q4: 에이전트 A의 MCP 설정을 에이전트 B가 쓸 수 있나?

**A**: 각 에이전트는 독립된 MCP 클라이언트를 가진다. 공유하려면 B의 프로필 파일(`~/.doorae/agents/b.yaml`)에 같은 MCP 서버를 추가한다.

### Q5: Doorae 서버가 MCP 서버 디스커버리를 돕나?

**A**: **아니다**. 디스커버리는 에이전트 프로필 파일(`~/.doorae/agents/*.yaml`)에서 관리된다. 서버는 에이전트의 MCP 설정을 모르며, 알 필요도 없다.

---

## 6.8 구현 체크리스트

이 문서에 해당하는 서버 측 구현은 거의 없다 — 바로 그것이 포인트다.

- [ ] `doorae/db/models.py`: `Message.extra_metadata` JSON 컬럼 존재 확인 (이미 포함)
- [ ] `doorae/ws/protocol.py`: `SendMessageFrame.metadata` 필드 존재 확인 (이미 포함)
- [ ] `doorae/messages/service.py`: `append_message()`에서 metadata를 검증 없이 저장 (이미 구현)
- [ ] `doorae/ws/manager.py`: 브로드캐스트 시 metadata 포함 (이미 구현)
- [ ] **문서화**: README.md에 "서버는 MCP를 모른다. MCP는 각 에이전트 엔진이 자체 처리" 문장 명시

SDK 측 구현:

- [ ] `doorae_sdk/integrations/*.py`: 엔진별 통합 시 MCP 설정은 **그대로 통과**시킨다. SDK는 MCP를 해석하지 않는다.
- [ ] `doorae_sdk/examples/`: MCP를 사용하는 에이전트 프로필 예시 파일 2-3개 제공

---

## 6.9 정리

| 질문 | 답 |
|---|---|
| 서버가 MCP 서버인가? | **아니다** |
| 서버가 MCP 클라이언트를 내장하는가? | **아니다** |
| 에이전트가 MCP를 쓸 수 있는가? | **그렇다** (엔진 자체 기능으로) |
| MCP 도구 결과를 채팅에 공유할 수 있는가? | **그렇다** (일반 `send_message`로, `metadata`에 태그) |
| 서버가 `metadata` 내용을 검증하는가? | **아니다** (자유형 JSON으로 저장) |
| 이 경계를 넘으면 무엇이 망가지는가? | 토큰 효율 30배 손실 + LLM 대화 턴 붕괴 + 서버 LOC 폭증 |

**한 문장 정리**: Doorae 서버는 채팅 프로토콜이고, MCP는 도구 프로토콜이다. 둘은 **목적이 다르며 경계를 넘지 않는다**. 이 경계를 지키는 것이 Plan A의 경량성을 유지하는 가장 중요한 실천이다.
