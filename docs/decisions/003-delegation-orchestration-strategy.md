# ADR-003: Delegation Orchestration Strategy

**날짜**: 2026-04-12
**상태**: 수락됨
**결정자**: Changyong Um

## 컨텍스트

Anygarden 에이전트가 메인룸에서 받은 작업을 서브룸으로 위임할 때, 오케스트레이션을 누가 얼마나 담당할지 결정해야 한다.

### 배경 분석: Claude Code 아키텍처

Claude Code 유출 코드 분석 (wikidocs.net/338204, github.com/ultraworkers/claw-code) 에서 도출한 핵심 패턴:

1. **트리 토폴로지**: 부모→자식 단방향. 자식끼리 대화 불가. 결과는 위로만 흐름.
2. **도구 제한이 핵심**: 서브에이전트 차별화는 대화 내용이 아니라 허용 도구셋 (Explore=읽기전용, Plan=읽기+Todo, 기본=전체).
3. **부모 LLM = 오케스트레이터**: 별도 오케스트레이터 서비스 없음. LLM 의 tool-use call 이 판단.
4. **구조화된 TaskPacket**: 자연어가 아닌 `{objective, scope, acceptance_tests, escalation_policy}` 명세.
5. **GreenContract**: 서브에이전트 결과를 LLM 이 아닌 실제 테스트로 검증.

### Anygarden 와의 차이

| | Claude Code (트리) | Anygarden (메시) |
|--|---|---|
| 토폴로지 | 부모→자식 단방향 | 룸 내 전체 브로드캐스트 |
| 에이전트 간 대화 | 불가 | 가능 (고유 강점이자 핑퐁 리스크) |
| 오케스트레이션 | 부모 LLM 직접 | 현재 없음 (모두 reactive) |

Anygarden 의 WebSocket 메시 토폴로지는 "에이전트 간 실시간 협업" 이라는 Claude Code 가 못 하는 고유 강점. 이를 살리되 안전하게 하는 전략이 필요.

## 검토한 선택지

### 옵션 1: LLM 판단 최소화 — delegation 판단만 LLM, 나머지 인프라

- LLM: "이건 서브룸으로 보내야 해" + TaskPacket 생성 (tool call)
- 인프라: 전달, 응답 대기, 메인룸 보고, 핑퐁 방지, timeout 검증

### 옵션 2: 풀 오케스트레이션 — LLM 이 전부 담당

- LLM: delegation 판단 + TaskPacket + 서브룸 에이전트 선택 + 결과 검증
- 인프라: 전달만

### 옵션 3: 하이브리드 — LLM + 검증 에이전트

- LLM: delegation 판단 + TaskPacket
- 인프라: 핑퐁 방지
- 별도 검증 에이전트: 결과 품질 체크

## 결정

**옵션 1: LLM 판단 최소화**

## 근거

1. **예측 가능성**: LLM 에게 적게 맡길수록 결과가 예측 가능. Claude Code 가 GreenContract 을 LLM 이 아닌 실제 테스트로 만든 이유와 동일.
2. **현재 인프라 활용**: 핑퐁 방지 (participant_id 필터) 이미 구현됨. delegate.py 의 전달/대기/보고 로직 이미 v1 으로 존재. LLM 에게 새로 줘야 할 건 `delegate` tool call 하나뿐.
3. **실패 범위 축소**: LLM 판단이 틀려도 (불필요한 delegation) 인프라가 안전하게 처리. 옵션 2 는 LLM 실수 시 전체 체인이 망가짐.
4. **점진적 확장**: v1 → v2 → v3 으로 LLM 역할을 점진적으로 늘릴 수 있음.

## 역할 분담

| 역할 | 담당 | 비고 |
|------|------|------|
| "이건 서브룸으로 보내야 해" 판단 | **LLM** | system prompt 규칙 + tool call |
| TaskPacket 생성 (objective, scope) | **LLM** | tool call 인자로 구조화 |
| 서브룸 전달 + 응답 대기 + 메인룸 보고 | **인프라** | delegate.py |
| 핑퐁 방지 | **인프라** | participant_id 필터 (구현 완료) |
| 결과 검증 v1 | **인프라** | 응답 존재 여부 + timeout |
| 결과 검증 v2 (향후) | **인프라** + 간단한 LLM 판정 | GreenContract 의 anygarden 버전 |

## 진화 경로

### v1 (현재): 명시적 /delegate 명령
- 사용자가 직접 `/delegate 서브룸 작업` 입력
- LLM 관여 없음 — 인프라가 파싱 + 전달 + 캡처

### v2 (다음): LLM 자동 판단
- system prompt 에 delegation 규칙 추가
- LLM 이 tool call 로 `delegate(sub_room, task_packet)` 호출
- 인프라가 실행 + 결과 보고

### v3 (향후): 구조화된 TaskPacket + 품질 게이트
- TaskPacket: `{objective, scope, acceptance_tests, escalation_policy}`
- 결과 검증: LLM 이 "이 응답이 objective 에 답하는가" 간단 판정
- 실패 시 escalation: 메인룸에 "서브룸에서 해결 못 함, 추가 지시 필요" 보고

### v4 (장기): 에이전트 간 협업 프로토콜
- LaneEvent 스타일 이벤트 (started, blocked, finished, failed)
- BranchLock 스타일 작업 영역 충돌 방지
- 에이전트별 도구 권한 등급 (explore / plan / implement)

## v2 세부 결정: LLM 자동 판단의 정보 소스

### 컨텍스트

v2 에서 LLM 이 "이 작업을 서브룸에 위임할지" 판단하려면 서브룸에 대한 정보가 system prompt (AGENTS.md) 에 있어야 한다. 어떤 정보를 넣을지 결정.

### 검토한 선택지

**A. 서브룸 이름만**: 이름이 곧 용도. "디자인검토" 면 디자인 작업 위임.

**B. 서브룸 이름 + 설명**: Room 에 description 필드 추가. admin 이 서브룸 생성 시 용도 기술.

**C. 서브룸 구성원 기반**: 서브룸의 에이전트 이름 + AGENTS.md 첫 줄(역할)을 읽어서 주입. LLM 이 구성원 역할을 보고 위임 대상 추론.

### 결정

**B (이름 + 설명)** 을 기본으로 채택. **C (구성원 기반)** 는 향후 옵션으로 보류.

### 근거

- B 는 admin 의도가 명확히 반영됨. "이 서브룸은 이런 작업을 위한 것" 이 설명에 담김.
- C 는 구성원이 바뀔 때 자동 반영된다는 장점이 있지만, 구성원 description 이 부실하면 판단 품질이 떨어짐. 또한 spawn frame 에 다른 에이전트의 AGENTS.md 를 포함하면 데이터 양이 늘어남.
- B 로 시작하고, 필요 시 C 를 옵션으로 추가하면 됨 (flag 로 활성화).

### 구현 — AGENTS.md 자동 인라인

`spawner.py::_compose_agents_md()` 가 이미 `## Available skills` 를 자동 인라인하는 패턴 존재. 동일하게 `## Delegation` 섹션을 추가:

```markdown
## Delegation

Sub-rooms you can delegate to using /delegate command:

- **디자인검토**: UI/UX 디자인 리뷰 및 피드백
  → /delegate 디자인검토 <task>
- **코드리뷰**: 코드 품질, 보안, 성능 리뷰
  → /delegate 코드리뷰 <task>

When a task matches a sub-room's purpose, delegate instead of
answering directly. Report the result back to this room.
```

**데이터 소스**: spawn frame 에 sub-room 정보 추가 필요.

- `lifecycle.py::request_start()`: 에이전트의 rooms 중 parent_room_id 가 있는 것 = 서브룸. 해당 서브룸의 name + description 을 spawn frame 에 포함.
- `spawner.py::_compose_agents_md()`: spawn frame 의 sub-room 정보로 Delegation 섹션 생성.

**Room description 필드**: `rooms` 테이블에 `description TEXT` 컬럼 추가 (migration 006). 기존 룸은 NULL.

### 향후 옵션: 구성원 기반 (C)

필요 시 Delegation 섹션에 구성원 정보를 추가:

```markdown
- **디자인검토**: UI/UX 디자인 리뷰
  Members: 디자인에이전트 (UI/UX 전문), 프론트엔드봇 (React 구현)
  → /delegate 디자인검토 <task>
```

활성화 조건: spawn frame 에 `include_member_descriptions: true` 플래그. 기본값 false.

## Claude Code 에서 채택하는 것 vs 안 하는 것

### 채택

- **TaskPacket 구조** (v3): objective + scope + acceptance 명세
- **도구 제한 개념** (v4): 에이전트별 권한 등급
- **결과 검증 레이어** (v3): GreenContract 의 경량 버전
- **LaneEvent 프로토콜** (v4): 구조화된 상태 이벤트

### 채택하지 않음

- **트리 토폴로지**: anygarden 의 메시(룸 브로드캐스트)는 고유 강점. 에이전트 간 실시간 협업 가능성 유지.
- **파일 기반 결과 전달**: anygarden 는 WebSocket 실시간 스트림 사용. 디스크 I/O 불필요.
- **완전한 샌드박스 격리**: anygarden 의 per-agent directory + workspace-write 가 이미 충분.

## 결과

- v1 (`/delegate` 명시적 명령) 이미 구현 완료
- v2 (LLM 자동 판단) 를 다음 단계로 구현
- 이 ADR 은 v2~v4 의 설계 기반이 됨
