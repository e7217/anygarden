# fix(rooms,agent): bypass ingest_only stamp for human senders; move orchestrator O1 ahead of stamp (#233)

- PR: #233
- Date: 2026-04-21
- Branch: `fix/233-orchestrator-ingest-only-ordering`

## Situation

`speaker_strategy=orchestrator` 방에서 `context_window_enabled=true`일 때 사용자가 멘션/프리픽스 없이 "분석해줘" 같은 맨몸 메시지를 보내면 orchestrator를 포함한 모든 에이전트가 침묵하는 회귀가 발견됐다(#225가 `context_window_enabled` 기본값을 `true`로 flip하면서 잠복 버그가 수면 위로 올라옴).

원인은 두 층에 걸쳐 있었다:

- **서버(`ws/handler.py::_is_ambient_candidate`)**: 발신자 종류를 구분하지 않고 "멘션이 없고 `[DELEGATED]`/`[ROOM_QUERY]`/`[HANDOFF]` 프리픽스도 없는 메시지 = ambient"로 판정해서 `metadata.ingest_only=true`를 찍었다. #148 Part 3의 원래 설계 의도는 "에이전트 간 잡담만 흡수"였는데 구현에서 이 조건이 빠져 있었다.
- **에이전트(`integrations/base.py::decide_policy`)**: 규칙 순서가 `(4) ingest_only 단락 → (6) strategy dispatcher O1`로, 서버가 스탬프를 찍은 순간 orchestrator가 자기 차례를 passive ingestion으로 흘려보냈다.

재현 방 스냅샷(`room4-api-state.json`): 사용자 메시지 3건 모두 `metadata.ingest_only=true`, `speaker_strategy=orchestrator`, `orchestrator_agent_id=agent01-claude`. Orchestrator가 깨어날 수 없는 상태였다.

## Task

- 서버에서 **사람(user/guest) 발신** 메시지는 어떤 조건에서도 `ingest_only` 스탬프를 찍지 않도록 `_is_ambient_candidate`에 발신자 가드를 추가.
- 에이전트에서 orchestrator O1(`내가 이 방 orchestrator면 RESPOND`)과 round_robin/orchestrator O2(`next_speaker_participant_id=me`)를 `ingest_only` 단락보다 앞으로 이동시켜, 서버가 실수로 스탬프를 찍어도 지명된 speaker는 응답하도록 방어(belt-and-suspenders).
- 기존 `test_ambient_broadcast_is_stamped_when_enabled`는 user 발신 stamp를 기대하는 **잘못된 전제**로 작성돼 있었으므로 재작성.

## Action

서버:

- `packages/cluster/doorae/ws/handler.py` — `_is_ambient_candidate` 시그니처에 `*, sender_is_agent: bool` 키워드 인자 추가. 함수 본문 최상단에 `if not sender_is_agent: return False` 가드와 docstring rule 0 추가. 호출부(라인 ~775)에서 `sender_is_agent = identity is not None and identity.kind == "agent"`를 계산해 전달하고 주석으로 #233 컨텍스트 명시.

에이전트:

- `packages/agent/doorae_agent/integrations/base.py::decide_policy` — 기존 규칙 3(direct mention)과 기존 규칙 4(ingest_only) 사이에 **새 규칙 4a "Strategy-forced RESPOND"** 삽입:
  - `strategy == "orchestrator"`이고 `orchestrator_agent_id == my_agent_id` → `RESPOND`
  - `strategy ∈ {"round_robin", "orchestrator"}`이고 `next_speaker_participant_id` ∈ `_my_participant_ids` → `RESPOND`
  - 이 두 케이스를 "서버가 이 에이전트에게 명시적 책임을 부여했다"는 단일 개념으로 묶음.
- 하류의 strategy dispatcher(라인 ~300-)에서 O1과 next_speaker=me 중복 평가 제거. `round_robin`은 reaching here이면 곧바로 `SKIP`, `orchestrator`는 O3만 남김. 주석으로 "이 조건은 4a로 올라갔음" 크로스레퍼런스.
- `decide_policy` 최상단 docstring을 새 규칙 순서(1 → 2 → 2d → 3 → 4a → 4 → 5 → 6a → 6 → 7)에 맞게 재작성.

테스트:

- `packages/cluster/tests/test_ws_handler.py::TestContextWindowBroadcast`:
  - 기존 `test_ambient_broadcast_is_stamped_when_enabled` → `test_user_ambient_broadcast_is_not_stamped`로 재작성(user 발신 → stamp 없음).
  - 신규 `test_agent_ambient_broadcast_is_stamped_when_enabled` — agent 토큰으로 접속·발송해 #148 Part 3 원래 의도(에이전트→에이전트 스탬프)가 살아있는지 검증.
  - 신규 `test_orchestrator_room_user_send_is_not_stamped` — 통합 시나리오. `speaker_strategy=orchestrator` + orchestrator 지정 + `context_window_enabled=true`에서 사용자가 맨몸 메시지 전송 → broadcast 메타데이터에 `ingest_only` 없음 확인.
- `packages/agent/tests/test_integrations/test_should_respond.py::TestStrategyForcedRespondBeatsIngestOnly` (신규 클래스) — 5개 케이스:
  - orchestrator → stamp 있어도 `RESPOND`
  - non-orchestrator → stamp대로 `INGEST_ONLY`
  - round_robin next_speaker=me → `RESPOND`
  - round_robin next_speaker=other → `INGEST_ONLY`
  - `context_window_opt_out=True` + orchestrator → `RESPOND` (모순 설정이지만 orchestrator가 멈추는 편보다 낫다)

## Decisions

대안은 `.tmp/plan-233-orchestrator-ingest-only-ordering.md` §3.2에 자세히 기록돼 있음. 요점만 남김.

수정 범위 — 세 가지:

- **C. 서버 + 에이전트 양쪽 수정 (채택)**. 한쪽만 고치면 각각 반쪽 해결이 됨: 서버만 고치면 O1 주석의 약속("including unaddressed human messages")이 여전히 코드와 불일치해 미래 회귀 재발 여지가 있고, 에이전트만 고치면 orchestrator는 응답해도 워커 에이전트들이 여전히 INGEST_ONLY 상태라 사람 발신을 "잡담"으로 오해한 채 방이 흘러감.
- A. 서버만 — 위와 같은 이유로 기각.
- B. 에이전트만 — 위와 같은 이유로 기각.

서버 가드 위치 — 두 가지:

- **A. `_is_ambient_candidate` 시그니처에 `sender_is_agent` 추가 (채택)**. 이 함수가 "ambient 여부"의 단일 진실이 됨. 내부 private 헬퍼(`_` prefix)이고 호출처가 1곳뿐이라 시그니처 변경 비용이 거의 없음.
- B. 호출부에서만 `if sender_is_agent and _is_ambient_candidate(...)` — 미래 호출자가 가드를 빼먹을 수 있어 의도가 숨음.

에이전트 규칙 5a 일반화 범위 — 세 가지:

- **B. orchestrator O1 + round_robin next_speaker 이동 (채택)**. 두 케이스 모두 "서버가 이 에이전트에게 명시적 책임 부여"라는 같은 의미라 하나의 단락 규칙으로 묶음.
- A. orchestrator O1만 이동 — round_robin에서 유사한 잠재 버그(`next_speaker` + stamp 동시) 미해결.
- C. 모든 strategy 분기를 ingest_only 앞으로 — O3("나는 orchestrator가 아니니 SKIP")를 앞으로 옮기면 ingest_only 흡수 기회를 놓침. 과잉 일반화.

테스트 전제 교정:

- 기존 `test_ambient_broadcast_is_stamped_when_enabled`는 user 발신으로 stamp를 기대하고 있었는데, #148 원 plan을 검토해보면 stamp의 설계 의도는 agent-to-agent였다. 이 테스트가 잘못된 전제였다고 판단해 재작성하고, agent 발신용 별도 테스트를 추가해 원래 의도를 명시적으로 보호.

가정 / 재평가 트리거:

- Round_robin에서 서버가 user ambient + `next_speaker` stamp를 **동시에** 찍는 실제 경로가 있다고 가정했음. 서버 수정으로 이 상황은 소멸하지만, 5a에 round_robin을 포함한 건 설계 일관성(두 케이스 모두 "strategy가 지명한 speaker")을 위함. 만약 round_robin의 `next_speaker`가 스탬프 없이 단독 도달하는 것만이 유일한 경로라면 이 확장은 무해한 redundancy.
- `context_window_opt_out=True`로 설정된 orchestrator는 모순 설정(“orchestrator로 지정해놓고 context_window는 끈다")이므로 5a가 opt_out을 이긴 것이 오히려 사용자 의도에 가깝다고 판단. 만약 실제로 이 조합이 의도적인 구성이라는 사용 사례가 생기면 5a에 opt_out 분기를 추가해야 함.

## Result

Orchestrator 방에서 사용자 맨몸 메시지가 orchestrator를 깨울 수 있게 복구됨. 에이전트→에이전트 잡담에 대한 `ingest_only` 스탬프(#148 Part 3 원래 의도)는 그대로 유지.

- cluster tests: 699 passed, 1 deselected (기존 699 → 0 regression; 새 2 통과 포함 — `test_user_ambient_broadcast_is_not_stamped`는 기존 `test_ambient_broadcast_is_stamped_when_enabled`의 rename + 전제 교정).
- agent tests: 265 passed, 1 failed. 실패 1건은 기존부터 있던 `test_openai.py::test_integrate_registers_handler` (OPENAI_API_KEY 누락). main에서도 동일 재현 → 본 PR과 무관.
- ruff: 수정 파일 내 신규 경고 없음(agent 쪽 전부 통과, cluster 쪽 잔존 경고는 사전 존재).
- mypy: 수정 파일에서 신규 오류 없음(3건 모두 사전 존재).

Deferred: 실제 `rooms.json` 스냅샷(`room4-api-state.json`)의 user 메시지 3건은 이미 DB에 stamp가 찍힌 상태로 남아 있음 — 이번 PR은 미래 메시지의 동작만 고치고 기존 row는 건드리지 않음. 필요하면 별도 admin 스크립트로 backfill 가능.
