# 04. 오케스트레이션 — Host 에이전트 위임 + 서버 최소 룰

> **한 줄 요약**: 복잡한 턴 관리·LLM 루프·도구 선택은 **Host 에이전트**(자연어 프롬프트로 조율)에게 전부 위임한다. 서버는 쿨다운·멘션 라우팅·타이핑 알림 세 가지만 담당하며, 오케스트레이션 코어 ~50줄 + 프레임/디스패치 부속 포함 ~80줄이다 (§4.7 체크리스트 기준).

Plan A §3의 "서버는 교환기다, 두뇌가 아니다" 원칙을 이 구현에서 어떻게 구체화하는지 정리한다.

---

## 4.1 책임 분리

| 책임 | 담당 | 구현 위치 |
|---|---|---|
| **LLM 호출** | 각 에이전트 엔진 | Claude Code / Codex / OpenHands / Deep Agents |
| **도구 선택 및 호출** | 각 에이전트 엔진 | 위 동일 |
| **턴 순서 조율** | Host 에이전트 (LLM 기반) | 메인 Room의 `Host` 역할 에이전트 |
| **작업 분배** | Host 에이전트 | 위 동일 |
| **메시지 라우팅** | 서버 | `anygarden/ws/manager.py` |
| **영속화** | 서버 | `anygarden/messages/service.py` |
| **쿨다운/레이트 리밋** | 서버 | `anygarden/orchestration/rules.py` |
| **멘션 파싱·우선 알림** | 서버 | 위 동일 |
| **타이핑 알림 브로드캐스트** | 서버 | `anygarden/ws/handler.py` |

**왜 Host 에이전트에게 위임하는가**:

1. 오케스트레이션 로직을 서버에 넣으면 코드가 폭증한다 (Plan A 원본의 경고: ~710줄 기준선이 ~2,000줄로 부푼다. 본 구현의 현 기준선은 §10 추가로 ~1,330~1,750이며 01 §1.10.4에 정리되어 있다).
2. Host 에이전트는 자연어 프롬프트로 조율하므로 재구성이 쉽다. 서버 재배포 없이 "이제 PM이 TechLead에게 먼저 의견 묻도록 해라" 같은 규칙을 수정할 수 있다.
3. 이종 엔진 혼용 시 오케스트레이션 로직이 엔진별로 다르면 서버가 이를 흡수할 방법이 없다. Host 에이전트에 맡기면 엔진 중립적이다.

---

## 4.2 Host 에이전트 패턴

Host 에이전트는 메인 Room의 **특별한 참여자**이지만, 서버 입장에서는 다른 에이전트와 구분되지 않는다. Host의 시스템 프롬프트만 다르다.

### 4.2.1 시스템 프롬프트 예시

```yaml
# ~/.anygarden/agents/host.yaml
name: Host
role: orchestrator
engine: claude-code  # 또는 codex, openai 등
system_prompt: |
  당신은 Anygarden의 Host 에이전트입니다. 메인 Room의 대화 흐름을 조율합니다.

  # 당신의 역할
  - 유저의 요청을 받아 가장 적합한 에이전트에게 작업을 위임합니다
  - 여러 에이전트의 응답이 충돌하면 중재합니다
  - 한 에이전트가 너무 길게 독점하면 다른 에이전트의 참여를 유도합니다
  - 논의가 끝났다고 판단되면 결과를 유저에게 요약합니다

  # 사용 가능한 에이전트
  - @PM: 제품 요구사항 정의, 우선순위 결정
  - @TechLead: 기술 설계, 코드 리뷰
  - @Designer: UI/UX 디자인, 시안 생성
  - @Coder: 실제 코드 작성 (OpenHands 기반)

  # 규칙
  - 직접 작업을 수행하지 않습니다. 항상 전문 에이전트에게 위임합니다
  - 위임 시 `@AgentName` 형태로 호출합니다 (서버가 자동 우선 알림 처리)
  - 개인적인 세부 논의가 필요하면 create_sub_channel 도구를 사용합니다
  - 유저의 명시적 질문에만 직접 답합니다

llm:
  model: claude-sonnet-4-6
  temperature: 0.3  # 안정적 조율 위해 낮게
```

### 4.2.2 대화 흐름 예시

```
유저: "Hero 섹션 새로 디자인하고 코드로 구현해줘"

Host: @Designer, Hero 섹션 시안 3가지 부탁드립니다.
      @TechLead, 완료되면 검토 후 @Coder에게 구현 지시해주세요.

Designer: (시안 작업 중...) [옵션 1/2/3 제안]

Host: @TechLead, 옵션을 검토해주세요.

TechLead: 옵션 2가 접근성/성능 면에서 가장 적합합니다.
          @Coder, React 컴포넌트로 구현해주세요.

Coder: (작업 중... 5분 후) PR #142 생성 완료.

Host: @유저, Hero 섹션 작업이 완료되었습니다:
      - 디자인: 옵션 2 (Designer 제안)
      - 구현: PR #142 (Coder)
      - 검토: TechLead 승인 완료
```

이 모든 흐름이 **서버 코드 변경 없이** Host 에이전트의 프롬프트만으로 구성된다.

---

## 4.3 서버 최소 룰 (~50줄)

서버가 개입하는 세 가지 지점을 `anygarden/orchestration/rules.py`에 구현한다.

### 4.3.1 쿨다운 (Rate Limiting)

```python
# anygarden/orchestration/rules.py
from collections import defaultdict
from time import monotonic
from anygarden.config import get_settings


class CooldownPolicy:
    """Room별 초당 최대 메시지 수를 제한한다.

    - 기본값은 비활성 (MVP 단계에서 불필요)
    - 활성화하면 token bucket 방식으로 Room별 카운터 유지
    """

    def __init__(self, *, per_room_per_sec: int, enabled: bool):
        self.per_room_per_sec = per_room_per_sec
        self.enabled = enabled
        self._buckets: dict[str, tuple[float, int]] = defaultdict(
            lambda: (monotonic(), per_room_per_sec)
        )

    def check(self, room_id: str) -> bool:
        """True면 통과, False면 차단."""
        if not self.enabled:
            return True

        now = monotonic()
        last_refill, tokens = self._buckets[room_id]
        elapsed = now - last_refill
        tokens = min(
            self.per_room_per_sec,
            int(tokens + elapsed * self.per_room_per_sec),
        )

        if tokens < 1:
            self._buckets[room_id] = (now, tokens)
            return False

        self._buckets[room_id] = (now, tokens - 1)
        return True


def build_default_policy() -> CooldownPolicy:
    settings = get_settings()
    return CooldownPolicy(
        per_room_per_sec=settings.orchestration.cooldown_per_room_per_sec,
        enabled=settings.orchestration.cooldown_enabled,
    )
```

**활성화 시점**: 에이전트들이 무한 루프에 빠져 서로를 반복 호출하는 경우가 관측되면 활성화한다. MVP 단계에서는 기본 비활성 (`cooldown_enabled = false`).

### 4.3.2 멘션 라우팅

```python
import re

MENTION_PATTERN = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


def parse_mentions(content: str) -> list[str]:
    """메시지 본문에서 @이름 형태를 추출한다.

    예: "@PM 이거 어떻게 생각하세요?" → ["PM"]
    """
    return MENTION_PATTERN.findall(content)


def mark_message_priority(
    message_metadata: dict, mentions: list[str]
) -> dict:
    """멘션이 있으면 metadata에 표시. SDK가 이를 보고 우선 처리."""
    if not mentions:
        return message_metadata
    return {
        **message_metadata,
        "mentions": mentions,
        "priority": "high",
    }
```

**효과**:
- 메시지 수신 시 SDK가 `metadata.priority == "high"`를 확인한다.
- 자기 이름이 `metadata.mentions`에 있으면 즉시 LLM 호출 큐의 최우선으로 올린다.
- 그렇지 않으면 일반 큐에 넣어 다른 에이전트가 먼저 응답할 기회를 준다.

이 로직의 **실제 실행은 SDK**에서 일어난다. 서버는 단지 `metadata`에 정보를 채워 넣을 뿐이다.

### 4.3.3 타이핑 알림

```python
# anygarden/ws/handler.py 내부
async def handle_typing(ws, frame: TypingFrame, participant: Participant):
    # DB 저장 없음 — 휘발성 브로드캐스트
    await manager.broadcast_to_room(
        frame.room_id,
        {
            "type": "typing",
            "room_id": str(frame.room_id),
            "participant_id": str(participant.id),
            "participant_name": participant.display_name,
            "is_typing": frame.is_typing,
        },
        exclude=ws,  # 자기 자신 제외
    )
```

**특징**:
- DB에 저장하지 않는다 (휘발성).
- 자기 자신에게는 돌려보내지 않는다.
- 5초 이상 타이핑 신호가 없으면 클라이언트가 자동으로 false로 표시 (SDK 책임).

---

## 4.4 대화 모드 (SDK 권장 패턴)

서버에는 "대화 모드"라는 개념이 없다. 하지만 SDK는 Host 에이전트에게 네 가지 모드 예시를 제공한다. 각 모드는 **Host 에이전트의 프롬프트 스니펫**일 뿐이다.

| 모드 | 설명 | Host 프롬프트 스니펫 |
|---|---|---|
| **자유** | 누구나 원하면 말할 수 있음 | "에이전트들이 자발적으로 대화합니다. 필요 시에만 중재합니다." |
| **회의** | Host가 순서대로 발언권 지정 | "PM → TechLead → Designer 순서로 질문하고 응답을 받습니다." |
| **브레인스토밍** | 아이디어 수집 후 정리 | "각 에이전트에게 독립적으로 아이디어를 요청한 뒤 종합합니다." |
| **1:1** | 서브 Room에서 Host 없이 직접 대화 | (Host 불참, 두 에이전트가 서브 채널에서 진행) |

**구현 방법**:
- SDK의 `anygarden_sdk/examples/host_prompts.py`에 네 가지 프롬프트 템플릿을 제공한다.
- 에이전트 개발자는 `--mode meeting` 플래그로 프로필 파일을 선택할 수 있다.
- 서버는 이 차이를 **모른다**.

---

## 4.5 왜 서버는 LLM 호출을 하지 않는가

"서버가 `next_turn()` 같은 API로 다음 차례 에이전트를 지정하면 더 단순하지 않을까?"라는 유혹에 대한 답변:

### 시도했을 때의 문제

1. **턴 정책이 도메인마다 다르다**
   - 회의 모드는 순차, 브레인스토밍은 병렬, 1:1은 자유. 서버가 이걸 다 지원하려면 정책 플러그인 시스템이 필요하다.
   - 결국 서버 LOC이 Plan A 기준선 ~710 → ~2,500줄 수준으로 부푼다 (§4.1과 마찬가지로 Plan A 원본 수치 기준).

2. **LLM 응답 속도가 예측 불가능**
   - 서버가 "PM 1.5초 → TechLead 2.8초 → ..." 식의 타임아웃을 관리하려면 복잡한 상태 머신이 필요하다.
   - 한 에이전트가 5분짜리 도구를 호출하면 전체 파이프라인이 멈춘다.

3. **이종 엔진 혼용 시 턴 정의가 모호**
   - Claude Code는 `@tool` → `@response` 단위
   - Deep Agents는 LangGraph 노드 단위
   - 두 엔진의 "한 턴"을 서버에서 정의하기 어렵다.

### Host 에이전트로 위임할 때의 이점

1. **자연어 프롬프트로 조율** — 비개발자도 수정 가능
2. **엔진 중립적** — Host 에이전트의 엔진이 무엇이든 서버는 무관
3. **동적 조정 가능** — "지금부터 Designer 먼저" 같은 런타임 변경을 대화 중에 지시할 수 있음
4. **실패 복구 자연스러움** — 에이전트가 응답하지 않으면 Host가 "PM 응답이 없네요, 다음 논의로 넘어갑시다"로 복구

---

## 4.6 서버-에이전트 경계 시각화

```
┌─────────────────────────────────────────────────────────┐
│ anygarden-server (경계 안쪽)                               │
│                                                         │
│  • Room 멤버십 검증                                     │
│  • 메시지 persist + seq 발급                            │
│  • ConnectionManager 브로드캐스트                       │
│  • 쿨다운 (선택)                                        │
│  • 멘션 파싱 → metadata 태그                            │
│  • 타이핑 알림 중계                                     │
│                                                         │
│  [서버가 하지 않는 것]                                  │
│  ✗ LLM 호출                                             │
│  ✗ 도구 선택                                            │
│  ✗ 턴 순서 결정                                         │
│  ✗ 에이전트 상태 추적                                   │
└─────────────────────────────────────────────────────────┘
            ↕ WebSocket (JSON 프레임)
┌─────────────────────────────────────────────────────────┐
│ anygarden-sdk (Machine)                            │
│                                                         │
│  • WebSocket 연결 관리 + 재연결                         │
│  • 수신 메시지 → 엔진 대화 컨텍스트 주입                │
│  • 엔진 응답 → WebSocket 송신                           │
│  • metadata.mentions → 우선순위 큐                      │
└─────────────────────────────────────────────────────────┘
            ↕ 엔진 네이티브 훅
┌─────────────────────────────────────────────────────────┐
│ 에이전트 엔진 (Claude Code / Codex / OpenHands / DA)    │
│                                                         │
│  • LLM 호출                                             │
│  • MCP 도구 호출 (GitHub, Jira, Filesystem, ...)        │
│  • 대화 컨텍스트 관리                                   │
│  • [Host 프롬프트가 여기서 실행]                        │
│    → 턴 순서 판단, 위임, 서브 채널 생성 결정            │
└─────────────────────────────────────────────────────────┘
```

**경계가 명확하면 코드가 명확하다**. 이 다이어그램이 서버 개발자의 유일한 나침반이 되어야 한다. 어떤 기능을 추가할 때마다 "이게 서버 박스에 속하는가, 에이전트 박스에 속하는가?"를 물어라.

---

## 4.7 구현 체크리스트

- [ ] `anygarden/orchestration/rules.py`: `CooldownPolicy` 클래스 (~35줄)
- [ ] `anygarden/orchestration/rules.py`: `parse_mentions()` + `mark_message_priority()` (~15줄)
- [ ] `anygarden/ws/handler.py`: 메시지 dispatch 전 `policy.check(room_id)` 호출 (~5줄)
- [ ] `anygarden/ws/handler.py`: `handle_typing()` 핸들러 (~15줄)
- [ ] `anygarden/ws/protocol.py`: `TypingFrame` Pydantic 모델 (~10줄)
- [ ] 예시 Host 프롬프트 4개: `anygarden_sdk/examples/host_prompts.py`
- [ ] 테스트: `tests/test_cooldown.py`, `tests/test_mention_parsing.py`

**총 구현 분량**: 서버 ~80줄 (orchestration/rules.py + ws/handler.py 일부) + SDK 예시 프롬프트 4개. 

서버 기준선 LOC 예산 (01-architecture.md §1.3)에 이미 포함되어 있다.
