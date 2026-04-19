---
title: 에이전트 컨텍스트 주입 분리 설계 (Ingest-Only Pathway)
created: 2026-04-19
status: implemented-stage-a
related:
  - docs/plans/2026-04-13-room-representative-agent-design.md
  - docs/plans/2026-04-13-mention-system-design.md
  - docs/research/2026-04-19-multi-agent-context-injection.md
---

> **구현 상태 (2026-04-19)**: Stage A(플래그 기반 명시 주입)가 Issue #74에서 구현·배포됨. 플래그 부착 위치는 서버 `ws/handler.py`가 아니라 **대표 에이전트의 `packages/agent/doorae_agent/integrations/room_query.py` `_deliver_result`** (cluster 코드 변경 0). Stage B(sliding-window ambient 흡수)는 관찰 기반 후속.

# 에이전트 컨텍스트 주입 분리

## 개요

멀티에이전트 룸에서 "응답 생성"과 "LLM 컨텍스트 주입"을 **독립된 두 결정으로 분리**한다. 현재 `should_respond`는 `bool`을 반환해 두 결정을 묶고 있으며, 결과적으로 대표 에이전트가 만들어낸 `[취합 결과]` 메시지, 다른 에이전트의 발언, 멘션 없는 사용자 메시지가 동일 룸 내 다른 에이전트의 LLM 세션에 주입되지 않아 대화 맥락에서 탈락한다.

본 설계는 `should_respond`를 3-state enum으로 확장하고, `EngineAdapter`에 `ingest_context(msg)` 후크를 추가한다. "경청만" 상태의 메시지는 다음 LLM 호출의 프롬프트 prefix로 자연어 요약 형태로 주입되어 세션 맥락의 일부가 된다. 응답 생성 경로는 트리거되지 않으므로 루프 폭주 위험이 없다.

## 배경과 문제

`docs/plans/2026-04-13-room-representative-agent-design.md`에서 도입된 `#룸` 멘션 → 대표 에이전트 취합 플로우는 동작하지만, 결과 메시지가 같은 룸의 다른 에이전트에게는 "UI에 보이지만 의식되지 않는" 상태가 된다. 원인은 수신 측 `should_respond`의 rule 6("발신자가 에이전트 + 멘션 없음 → skip")이 `on_message` 호출 자체를 차단해, Claude Agent SDK의 `resume=session_id` 세션에 해당 메시지가 들어가지 않기 때문이다.

문제는 `[취합 결과]`에 국한되지 않는다. 같은 게이트가 다음 경우 모두에 작동한다.

- 사용자 간 사담이 에이전트 컨텍스트에 반영되지 않음 (룸의 흐름을 에이전트가 모름)
- 다른 에이전트의 응답이 제3 에이전트의 세션에 들어가지 않음 (협업 불가)
- 멘션 없이 자연스럽게 흐르는 대화는 에이전트에게 사라진 것과 같음

연구 리포트 `docs/research/2026-04-19-multi-agent-context-injection.md`는 이 공백이 엔진 SDK 세션에 기반한 Doorae 아키텍처의 구조적 귀결임을 18개 소스로 교차 검증했다. 업계 표준(AutoGen, LangGraph, CAMEL, AgentVerse)은 "공유 히스토리 + 스피커 선택" 모델이고, 2025 신작(Intrinsic Memory Agents 2508.08997)은 per-agent 메모리와 공유 공간을 명시 분리한다. 두 흐름 모두 수신과 응답을 독립 결정으로 다룬다.

## 핵심 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 게이트 반환 타입 | `bool` → `MessagePolicy = RESPOND | INGEST_ONLY | SKIP` | 두 결정의 분리 명시화. 기존 호출처 하위 호환 유지(RESPOND/SKIP은 `bool` 캐스팅으로 매핑) |
| 주입 트리거 | 서버가 부착한 `metadata.ingest_only=true` 또는 `metadata.room_query_result` | Addressee recognition을 LLM에 위임하지 않음. 서버측 명시 파싱이 벤치마크상 훨씬 견고 (arXiv 2501.16643) |
| 주입 형식 | 자연어 1행 요약을 **다음 LLM 호출 프롬프트 prefix** 로 덧붙임 | Intrinsic Memory Agents의 경량 모사. JSON 슬롯까지 재현할 필요 없음 |
| 저장 위치 | 어댑터 인스턴스 내 `pending_context: list[str]` 버퍼 | 룸별 격리. 세션과 독립 생명주기 |
| 비우기 시점 | 다음 `query()` 호출 직후 (주입에 성공한 경우에만) | 실패 시 재시도 가능 |
| 엔진 범위 | Claude Code 우선 → Codex/Gemini/OpenAI 순차 | Claude SDK의 `resume` 세션 모델이 가장 영향 큼. Stateless 엔진은 단순히 메시지 리스트에 추가 |
| 루프 방지 | 주입은 LLM 호출을 트리거하지 않음 | 응답 생성 경로와 완전 분리. 기존 `[ROOM_QUERY]`/`[DELEGATED]` 경로와 독립 |

## 범위

### 이번 설계의 단기 범위

- `should_respond` 3-state 전환과 기존 호출처 마이그레이션
- `EngineAdapter` 인터페이스에 `ingest_context(msg)` 추상 메서드 추가
- Claude Code 어댑터에서 pending context 버퍼 + 다음 `query()` 프롬프트 prefix 주입
- `[취합 결과]` 메시지의 서버측 `metadata.ingest_only=true` 플래깅
- 테스트: `should_respond` 단위 테스트, 프롬프트 prefix 주입 시나리오, 기존 `[ROOM_QUERY]` 플로우 회귀

### 범위 밖 (후속 설계)

- **중기**: `session_mode: "sdk_resume" | "stateless_history"` 옵션화. AutoGen/LangGraph식으로 매 턴 룸 히스토리를 직접 조립.
- **장기**: MCP Observer/Pub-Sub 패턴 기반 blackboard 서버. 에이전트별 subscription 정책.
- 자동 addressee 추론 (채택 안 함 — GPT-4o 벤치마크상 우연 수준)

## 서버 흐름 변경

`packages/cluster/doorae/ws/handler.py`의 메시지 broadcast 직전에 `metadata.room_query_result`가 이미 부착된 메시지는 추가로 `metadata.ingest_only=true`도 함께 설정한다. 이는 수신 어댑터가 동일 플래그 하나로 "응답 안 함 + 주입 함" 상태를 인식하게 하기 위한 정규화다.

```python
# execute_room_query 내부 _deliver_result 직전
metadata["ingest_only"] = True
```

새 플래그는 받는 쪽이 인식하지 못하면 기존 동작(rule 6 skip)을 그대로 수행하므로 롤아웃 안전하다.

## SDK 흐름 변경

`packages/agent/doorae_agent/integrations/base.py`의 `should_respond` 반환 타입을 enum으로 전환한다.

```python
from enum import Enum

class MessagePolicy(Enum):
    RESPOND = "respond"
    INGEST_ONLY = "ingest_only"
    SKIP = "skip"

def decide_policy(msg, client) -> MessagePolicy:
    ...
    # rule 2b 직후 삽입
    metadata = msg.get("metadata") or {}
    if metadata.get("ingest_only"):
        return MessagePolicy.INGEST_ONLY
    ...
```

기존 `should_respond(msg, client) -> bool`는 하위 호환 래퍼로 유지해 점진 마이그레이션을 가능하게 한다.

```python
def should_respond(msg, client) -> bool:
    return decide_policy(msg, client) == MessagePolicy.RESPOND
```

`EngineAdapter` ABC에 새 메서드 추가:

```python
class EngineAdapter(ABC):
    @abstractmethod
    async def on_message(self, msg: dict[str, Any]) -> str | None: ...

    async def ingest_context(self, msg: dict[str, Any]) -> None:
        """응답 생성 없이 메시지를 다음 턴 컨텍스트로 주입한다.
        기본 구현은 no-op. 세션형 어댑터는 override."""
```

### Claude Code 어댑터 구현

`ClaudeCodeAdapter`는 `_pending_context: dict[str, list[str]]`를 룸별로 유지한다. `ingest_context`는 메시지를 자연어 요약으로 변환해 버퍼에 누적한다.

```python
async def ingest_context(self, msg: dict[str, Any]) -> None:
    room_id = msg.get("room_id", "_default")
    summary = self._format_context_line(msg)
    self._pending_context.setdefault(room_id, []).append(summary)

def _format_context_line(self, msg) -> str:
    # [취합 결과] 는 특별 처리, 그 외는 발화자 + 요약
    content = msg.get("content", "")
    meta = msg.get("metadata") or {}
    if "room_query_result" in meta:
        rq = meta["room_query_result"]
        return f"[참고] 룸 {rq['target_room_id']}에서 다음 응답이 왔습니다: {content[:300]}"
    return f"[참고] 다른 참여자 발언: {content[:200]}"
```

`on_message`의 프롬프트 조립 단계에서 버퍼 내용을 prefix로 소비한다.

```python
async def on_message(self, msg) -> str | None:
    room_id = msg.get("room_id", "_default")
    pending = self._pending_context.pop(room_id, [])
    prefix = "\n".join(pending)
    prompt = f"{prefix}\n\n{msg['content']}" if pending else msg["content"]
    ...
```

`pop` 직후 버퍼가 비워지므로 한 번 사용한 컨텍스트는 다음 턴에 재주입되지 않는다. Claude Agent SDK의 `resume` 세션이 자동 누적하기 때문에 중복 주입은 명시적 피해야 한다.

### 통합 지점

`integrate_with_claude_code`의 `_handle`에서 3-state 분기:

```python
policy = decide_policy(msg, client)
if policy == MessagePolicy.SKIP:
    return
if policy == MessagePolicy.INGEST_ONLY:
    await adapter.ingest_context(msg)
    return
# RESPOND 경로는 기존과 동일
```

Gemini/Codex 어댑터는 초기 구현에서 `ingest_context`의 기본 no-op을 유지한다. 이는 기능 회귀가 아니며, 해당 엔진을 쓰는 사용자는 현재와 동일한 경험을 얻는다. 후속 스프린트에서 엔진별로 구현을 채워간다.

## 테스트 전략

1. **`decide_policy` 단위 테스트**: 6 규칙 각각 + `ingest_only` 플래그 케이스. 기존 `should_respond` 테스트 유지하며 래퍼 회귀 없음 확인.
2. **Claude Code 어댑터 주입 테스트**: `ingest_context` 호출 → 이후 `on_message` 호출 시 prompt에 prefix 포함되는지 검증. SDK query 는 mock.
3. **`[취합 결과]` 플로우 통합 테스트**: 테스트룸1 → 테스트룸2 → 취합 결과 전달 시, 테스트룸1의 다른 에이전트가 다음 턴에 관련 정보를 언급할 수 있는지 프롬프트 검사 기반으로 확인.
4. **루프 안전성**: `ingest_only` 메시지를 받아도 응답이 브로드캐스트되지 않는지 회귀 방지 테스트.
5. **기존 테스트**: `packages/cluster/tests/test_ws_handler.py`, `packages/agent/tests/test_integrations/test_room_query.py`의 회귀 확인.

## 마이그레이션 및 호환성

- `should_respond` 래퍼 함수가 기존 호출을 모두 흡수하므로 단일 PR 내에서 안전 전환 가능
- `metadata.ingest_only` 플래그를 수신 측이 모르면 기존 rule 6으로 fallback되어 회귀 없음
- 기본 `EngineAdapter.ingest_context`는 no-op이라 미구현 엔진도 빌드 실패 없음
- `[ROOM_QUERY]` 및 `[DELEGATED]` 경로는 별도 metadata 키 (`room_query`, `delegated`)로 분리되어 있어 이번 변경의 영향 밖

## 리스크와 완화

| 리스크 | 영향 | 완화 |
|---|---|---|
| Claude SDK resume 세션에 prefix가 예상 외 동작 | 응답 품질 저하 | 프로토타이핑 단계에서 SDK 실제 호출로 검증. 필요 시 "이전 룸 상황 요약"을 system prompt 추가분으로 분리 |
| 버퍼가 대화 간 stale context로 커짐 | 프롬프트 크기 팽창 | `pending_context` 크기 상한(예: 10건/룸) 및 TTL (예: 10분). 초과 시 오래된 것부터 드롭 |
| 동일 메시지가 `ingest_context` 후 바로 응답 트리거로 다시 올 경우 중복 | 프롬프트에 중복 등장 | `decide_policy`의 분기는 상호 배타 (`ingest_only`는 `RESPOND` 보다 먼저 평가). 테스트로 보장 |
| 다국어 룸에서 prefix 포맷 문제 | 가독성/프롬프트 혼선 | `_format_context_line`은 룸 메시지 원문 언어를 그대로 쓰며 한국어·영어 모두 허용 |

## 구현 순서

1. `decide_policy` + `MessagePolicy` enum, `should_respond` 래퍼 (단위 테스트 포함)
2. `EngineAdapter.ingest_context` 기본 구현 (no-op)
3. `ClaudeCodeAdapter`에 버퍼 + `_format_context_line` + `on_message` prefix 주입
4. `integrate_with_claude_code._handle` 3-state 분기
5. 서버 `execute_room_query._deliver_result`에 `metadata.ingest_only=true` 부착
6. 통합 테스트 (취합 결과 → 제3 에이전트 context 반영)
7. `docs/research/2026-04-19-multi-agent-context-injection.md` 권장 §5 단기 항목 완료 플래그

구체 파일별 변경 경로와 TDD 스텝은 후속 `/worktree-plan` 산출물(`.tmp/plan-*.md`)에서 결정한다.

## 참고

- 연구 근거: `docs/research/2026-04-19-multi-agent-context-injection.md` (18 sources, 15 evidence rows)
- 원 설계: `docs/plans/2026-04-13-room-representative-agent-design.md`, `2026-04-13-mention-system-design.md`
- 직접 학술 레퍼런스: Intrinsic Memory Agents (arXiv 2508.08997, 2025)
- 업계 표준 대조: AutoGen GroupChat, LangGraph MessagesState
- Addressee 결정의 서버측 명시 파싱 유지: arXiv 2501.16643 벤치마크 근거
