# Design: A→B 인과 링크 — 지목된 에이전트 턴 추적 + parent_request_id

> Issue: #431 | Date: 2026-06-09 | 상태: 승인됨 (Approach A)

## 1. 문제

현재 `request_id` 모델은 **사용자 발 턴만** 추적한다. 사용자가 메시지를 보내면
방의 각 에이전트마다 `request_id`를 발급(`message_received` → `handler_*` →
`response_sent`)하지만, 에이전트 A의 답신이 에이전트 B를 깨우는
**에이전트→에이전트 턴은 전혀 추적되지 않는다**. 그 결과:

- B의 턴은 `request_id` 없이 처리되어 ActivityLog/트레이스에 안 보인다.
- RoomActivityDialog(#429)의 room flow 뷰에서 A→B 인과가 끊긴다.
- Langfuse 세션은 `room_id`로 묶이지만(같은 대화), 어느 턴이 어느 턴을
  **트리거했는지**(causal edge)는 알 수 없다.

## 2. 접근 비교

| 접근 | 설명 | 기각/채택 |
|---|---|---|
| **A. 지목된 에이전트만 발급** | 에이전트 send 시 `next_speaker_participant_id`가 가리키는 **단 1명**에게만 turn 발급 | **채택** |
| B. 모든 에이전트에 발급(user send처럼) | 에이전트 send마다 방의 모든 에이전트에 `message_received` 발급 | 기각 |
| C. message_id→request_id 캐시 후처리 | 별도 캐시로 사후 인과 추론 | 기각 |

**B 기각 사유**: 에이전트 대화는 user send보다 훨씬 빈번하다(턴마다 왕복).
에이전트 send마다 방의 N개 에이전트 전부에 turn을 발급하면, 실제로는 1명만
응답하므로 나머지 N-1개는 곧바로 orphan으로 수거된다 → ActivityLog가 phantom
orphan으로 범람하고 orphan 메트릭(#427)이 무의미해진다. user send는 사용자
입력당 1회뿐이라 N개 발급이 허용 범위지만, agent send는 그렇지 않다.

**C 기각 사유**: 클러스터는 이미 `next_speaker_participant_id`로 **누가 다음
화자인지 권위 있게** 알고 있다(handoff/round-robin/fallback이 여기로 수렴).
사후 캐시 추론은 이 권위 정보를 버리고 타이밍 휴리스틱으로 재구성하는 셈 —
불필요한 캐시·만료·miss를 도입한다. 동기 흐름에서 권위 신호를 직접 쓰는 게 단순·정확.

**A 채택 결정적 근거**: `next_speaker_participant_id`는 이미 메시지 append
**전에** 모든 오케스트레이션 경로(round_robin 1173, handoff 1199→283, orchestrator
fallback 1222)에서 설정된다. 그 1명에게만 발급하면 phantom 0, 캐시 0,
와이어 프로토콜 무변경으로 정확한 인과를 얻는다.

## 3. 설계

### 3.1 handler.py — 타게팅 fan-out
에이전트 send의 `response_sent` 기록 직후(`ws/handler.py` ~1296):

1. `metadata.next_speaker_participant_id`를 읽는다. 없으면 → fan-out 없음(phantom 0).
2. 그 participant가 (a) 에이전트이고 (b) 발신자(`participant.id`)와 다르면:
   - 새 `rid = uuid4()` 발급
   - `request_id_by_participant[next_pid] = rid` 등록 → `_make_out`이 B의 tailored
     브로드캐스트에 `meta.request_id=rid` 주입(user send과 동일 메커니즘) → B의
     LifecycleFrame이 rid로 thread back → **B 턴 추적됨**
   - `message_received` ActivityLog 작성: `request_id=rid`, `room_id`,
     `trigger_message_id=msg.id`(A의 메시지), **`parent_request_id=echoed_rid`**(A의 턴)
   - `tracing.start_request(rid, room_id, agent_id=next_aid, parent_request_id=echoed_rid)`

self-handoff(`next_pid == participant.id`)는 발급하지 않는다(턴 자기 루프 방지).

### 3.2 tracing.py — FOLLOWS_FROM span link
`start_request`에 `parent_request_id: Optional[str] = None` 추가:

- `parent_request_id`가 registry에 **살아있으면**(A의 root는 A의
  `handler_finished` 전까지 열려 있고, B 발급은 A의 `response_sent` 시점 —
  즉 동기적으로 더 이르다 → 항상 hit) parent root의 span_context로
  `links=[ot_trace.Link(ctx)]`를 만들어 root span 생성.
- 트레이스 백엔드(Langfuse)는 B를 A에 FOLLOWS_FROM으로 연결된 **별도 trace**로
  본다(부모-자식 X — A·B는 독립 수명. B를 A의 자식으로 넣으면 A의 root가
  먼저 닫혀 수명 불일치). `langfuse.session.id=room_id`가 둘을 한 세션으로
  묶고, Link가 명시적 인과 엣지를 더한다.
- **FOLLOWS_FROM은 타입 있는 Link로 표현**: OTEL에는 내장 FOLLOWS_FROM Link
  종류가 없어 맨 `Link(ctx)`는 무타입(자식-of와 구분 불가). 링크 속성
  `opentracing.ref_type="follows_from"`(OpenTracing→OTEL 브리지 표준 키)을 달아
  인과 관계를 명시한다.
- 캐시 불필요(동기 흐름). parent가 이미 닫혔으면(드묾) Link만 생략하고
  `anygarden.parent_request_id` 속성은 그대로 스탬프.
- **샘플링 주의**: B는 독립 root trace이고 OTEL의 `ParentBased(TraceIdRatioBased)`
  샘플러는 link를 샘플 결정에 쓰지 않는다. 따라서 `otel_sampling_ratio < 1.0`이면
  A·B가 독립적으로 keep/drop되어 인과 엣지가 끊길 수 있다(A만 남거나, B의 링크가
  drop된 A를 가리킴). 기본값 1.0에서는 항상 둘 다 keep되므로 잠재적. ratio를 낮추는
  운영자는 인과 링크가 1.0에서만 신뢰 가능함을 인지해야 한다.

### 3.3 frontend — parent 링크 표시
- `ActivityPanel.splitLogs`: `Turn.parentRequestId`를 `message_received`
  triggerRow의 `details.parent_request_id`에서 파생.
- `RoomActivityDialog`: turns를 `requestId→turn` 맵으로 만들어, parentRequestId가
  같은 방의 알려진 턴을 가리키면 "↳ <parent agent>"를 표시(A→B 화살표).

## 4. 데이터 흐름

```
사용자 → A: message_received(rid-A) → A handler → A response_sent(rid-A)
                                          │
                                          ├─ (#431) next_speaker=B?
                                          │   └─ message_received(rid-B,
                                          │        parent_request_id=rid-A,
                                          │        trigger_message_id=A.msg)
                                          │      start_request(rid-B, Link→rid-A.root)
                                          └─ A handler_finished(rid-A) → root 닫힘
B가 브로드캐스트(meta.request_id=rid-B) 수신 → B handler → B response_sent(rid-B) → …
```

## 5. 가정과 미해결

- **지목 후 SKIP**: B가 지목됐지만 정책상 응답 안 하면 `message_received`
  단독 행이 남는다. orphan sweeper는 `handler_started`가 있는 턴만 수거하므로
  (HAVING `started>0`) 이 행은 **DB 차원에서 수거되지 않고**(따라서
  `agent_turns_orphaned_total`에도 안 잡힘) flow 뷰에 `in_flight`로 영구히
  남는다. 단, tracing 쪽 root span은 TTL 기반 in-memory reaper가 orphaned로
  닫는다. 이는 user send의 비응답 에이전트(N개 발급 중 1개만 응답) 행동과
  동질인 기존 특성이며 #431이 새로 만든 결함이 아니다. silent-nomination
  회계가 필요해지면 sweeper를 `message_received`-only 턴까지 확장하는 별도 후속.
- **nomination 출처**: fan-out은 디스패처 헬퍼(round_robin/handoff/fallback)가
  이번 send에 반환한 **서버 권위 값**(`nominated_pid`)으로만 동작한다. 인바운드
  `metadata.next_speaker_participant_id`(에이전트가 위조 가능)나 영속
  `Room.next_speaker_participant_id`(이전 send의 stale 값)는 쓰지 않는다.
- **멀티 지목**: `next_speaker_participant_id`는 단일. 동시 다중 지목은 범위 외
  (필요 시 후속).
- **우회 트리거**: handoff/round-robin/fallback을 우회해 B가 깨는 경로가 있으면
  `next_speaker`가 비어 링크 누락(degrade, 에러 아님).

## 6. 검증

- 단위(tracing): `start_request(parent_request_id=)` → B root에 Link 1개 +
  `anygarden.parent_request_id` 속성, parent root 닫힌 뒤에도 Link 유효.
- 통합(handler): round_robin 2-에이전트 방에서 A send → B `message_received`에
  `parent_request_id`/`trigger_message_id` 스탬프 + 새 rid. 지목 없으면 무발급.
- 단위(frontend): `splitLogs`가 `parentRequestId` 파싱.
- 회귀: cluster pytest, frontend `npm run build` + vitest, ruff.

## 7. 참고
관련 #420·#422·#425·#427·#429. 트레이싱 코어 `observability/tracing.py`,
fan-out `ws/handler.py`, flow 뷰 `frontend/.../RoomActivityDialog.tsx`.
