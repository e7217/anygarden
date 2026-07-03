# 에이전트 응답불가 사유 관찰성 & 원클릭 복구 — 설계

- 작성일: 2026-07-03
- 상태: 설계 검토 대기
- 관련 배경: 직전 조사 3건 — (1) liveness 감지 실태, (2) "메시지 보냈는데 응답·progress 없음" 원인, (3) "엔진이 바뀌어서 무응답" 대응 부재
- 트리거 사례: DB 마이그레이션(#506 `codex`→`codex-cli`, #050/#035)으로 에이전트 엔진이 바뀐 뒤, 재spawn/재배치가 자동 반영되지 않아 에이전트가 조용히 미기동 상태로 남고 사용자·admin 누구도 원인을 알 수 없었음.

## 1. 문제 정의

에이전트가 "실제로는 응답할 수 없는 상태"인데 **아무도 그 사실·원인을 알려주지 않는다.** 사용자는 메시지를 보내고 침묵만 겪고(응답도 progress 표시도 없음), admin 목록에는 사유 없는 `pending` 배지만 뜬다. 현재 자동 감지·알림·복구 중 어느 것도 존재하지 않으며, 유일한 정상 복구 경로(`POST /api/v1/agents/{id}/start`)조차 원인 표시가 없어 admin이 무엇을 왜 눌러야 하는지 알 수 없다.

핵심 근거(직전 조사에서 검증):
- 마이그레이션은 `agents.engine` 컬럼만 UPDATE, generation/desired_state/프로세스 미접촉 (`db/migrations/versions/050_migrate_codex_to_codex_cli.py`).
- 머신 수렴 게이트가 generation-only라 엔진 값 변경만으로는 재spawn 안 됨 (`packages/machine/anygarden_machine/daemon.py` `if current_gen >= manifest.generation: return`).
- placement 실패(`NoSuitableMachineError`, `scheduler/placement.py`)를 `request_start` except가 삼키고 `actual_state="pending"`만 커밋 — `last_crash_reason`도 ActivityLog도 안 남기는 유일한 무음 분기 (`scheduler/lifecycle.py:140-148`).
- 배치조차 안 된 에이전트는 `handler_started`가 없어 기존 orphan 방 공지(`_ORPHAN_NOTICE_TEXT`, `scheduler/lifecycle.py:1070`) 대상에서도 제외 → 완전한 침묵.

## 2. 범위

**포함 (미기동 계열 전체):** 엔진 변경, 지원 머신 없음(no_machine), spawn 실패, crash 미복구, 어느 방에도 미소속(no_room), 엔진 불일치(divergence).

**제외:** hang("프로세스는 살아있으나 먹통") — 능동 헬스 프로브가 없어 별도 설계가 필요. 이번 범위 밖.

**대상 사용자 (깊이 차등):**
- **admin** — 원인 상세(stderr 포함) + 원클릭 복구까지.
- **방 참여자(end-user)** — "이 에이전트는 지금 응답 불가"라는 가벼운 인지(반응형 방 공지 + 상시 배지). stderr 등 상세는 노출 안 함.

**복구:** 관찰성 + admin 원클릭 복구. **백그라운드 자동 재시도는 하지 않음**(human-in-the-loop, 스케줄러 로직 최소 변경).

## 3. 아키텍처 개요 (Approach A: 사유를 에이전트 1급 필드로)

단일 진실원(single source of truth)을 Agent 레코드에 두고, 스케줄러의 모든 "미기동 전이 지점"에서 채운다. 세 표면(admin UI, 사용자 사이드바 배지, 반응형 방 공지)이 **모두 같은 필드**를 읽는다. spawn 실패 stderr처럼 실패 순간 머신에서만 아는 정보도 필드로 전파해 담는다.

```
[스케줄러 전이 지점] --write--> Agent.unavailable_* (단일 진실원)
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
   REST AgentOut               WS presence/welcome          message-send handler
   (admin 상세)                (coarse available+label)       (반응형 방 공지)
        |                            |                            |
   admin 배지+hover           사용자 사이드바 점 상태          방 시스템 메시지
   + 원클릭 재시작            (상시 예방)                     (겪는 순간 설명)
```

## 4. 데이터 모델

`Agent` 테이블에 3개 컬럼 추가 (`packages/cluster/anygarden/db/models.py`, 신규 alembic 마이그레이션):

| 컬럼 | 타입 | 용도 |
|---|---|---|
| `unavailable_code` | `String(64) \| None` (indexed) | 기계용 사유 코드. `NULL` = 정상 |
| `unavailable_detail` | `JSON \| None` | 엔진명, stderr_tail, machine_id, exit_code 등 부가정보 |
| `unavailable_since` | `DateTime(timezone=True) \| None` | 고착 지속시간 계산용 |

- 사람용 메시지는 **저장하지 않음** — 서버가 `code + detail`에서 파생(향후 i18n 유연성 확보). 파생 함수 `render_unavailable_message(code, detail, *, audience) -> str`를 신설(`scheduler/` 또는 `agents` 공용 모듈). `audience="admin"`은 stderr 포함, `audience="user"`는 짧은 라벨만.
- 기존 `last_crash_reason`(Text, `models.py:247`)은 crash 상세 free-text로 유지. `unavailable_code`가 그 위 상위 분류를 담당(둘은 공존).
- 인덱스: `unavailable_code` 단일 인덱스(“no_machine인 에이전트 전부” 같은 admin 조회 대비).

### 사유 코드 분류

| code | 트리거 | detail 키 | audience=user 라벨(예) |
|---|---|---|---|
| `no_machine_for_engine` | placement가 엔진 지원 online 머신 못 찾음 | `engine` | "엔진을 지원하는 실행 환경이 없어 대기 중" |
| `spawn_failed` | 머신이 spawn 시도했으나 실패(Unknown engine·바이너리 부재) | `engine`, `stderr_tail` | "실행 환경 시작에 실패해 대기 중" |
| `engine_mismatch` | DB `engine` ≠ 실행 중 프로세스 engine | `db_engine`, `running_engine` | "설정 변경 반영을 위해 재시작이 필요" |
| `crashed` | crash 후 미복구(crash budget 소진/restart_policy) | `exit_code`, `stderr_tail` | "오류로 중단됨" |
| `no_room` | 어느 방에도 미소속 | — | "배정된 방이 없음" |

`desired_state == "stopped"`(admin 의도적 종료)는 "문제"가 아니므로 **사유를 세팅하지 않는다.**

### 클리어 규칙

사유는 "현재 조건"으로 (재)평가한다 — 각 관련 전이/리포트에서 조건이 사라지면 `NULL`로 리셋:
- **성공 running 전이(handler_started)** 시, 그 전이가 조건을 해소했으면 클리어. 단 `engine_mismatch`는 running이어도 성립하는 사유이므로, running 전이만으로 무조건 클리어하지 않고 **리포트된 engine이 DB engine과 일치할 때만** 클리어(불일치가 남아있으면 유지/재세팅).
- admin이 의도적 stop (`request_stop` / `desired_state=stopped`) 시 클리어.

즉 클리어는 상태 코드(actual_state) 단독이 아니라 **각 사유의 조건 해소 여부**로 판단한다.

## 5. 백엔드 write 경로

사유를 채우는 전이 지점 (모두 `packages/cluster/anygarden/scheduler/lifecycle.py` 중심):

1. **no_machine (핵심)** — `request_start`의 `NoSuitableMachineError` except (`lifecycle.py:140-148`):
   - `unavailable_code="no_machine_for_engine"`, `unavailable_detail={"engine": agent.engine}`, `unavailable_since=now`.
   - **추가로 `placed_on_machine_id=None`으로 리셋** — 이후 머신이 register하면 `_place_orphaned_agents`의 `placed_on_machine_id IS NULL` 필터가 자동 재배치 후보로 집어감(복구 정확성). (직전 조사의 medium gap 2건 동시 해소.)
   - 감사 로그: `ActivityLog(event_type="agent_unavailable", details={code, detail})` 1행.
2. **spawn_failed** — 머신 daemon이 초기 spawn 실패 시 stderr tail을 `AgentActual.last_crash_reason`(스키마 필드 이미 존재, `packages/machine/anygarden_machine/protocol/frames.py:225`)에 채워 보고하도록 daemon 수정(`daemon.py` 초기 spawn 실패 경로 `562-574`, 상태 리포트 `744-768`). 서버 핸들러는 이미 이 필드를 읽음(`lifecycle.py:350-351`) → spawn 실패 actual 리포트를 `unavailable_code="spawn_failed"` + `stderr_tail`로 매핑.
3. **crashed (미복구)** — crash budget 소진 / restart_policy로 재시작 안 하는 분기에서 `unavailable_code="crashed"`.
4. **engine_mismatch (값싼 piggyback)** — 스케줄러가 머신 리포트/sync를 처리할 때 `agent.engine`과 실행 중 프로세스가 보고한 engine(또는 #447의 최신 `ActivityLog.engine`)을 대조. `actual_state=running`인데 diverge면 `unavailable_code="engine_mismatch"`. **신규 백그라운드 루프 없음** — 기존 리포트 처리 흐름에 비교 한 줄.
5. **clear** — handler_started로 running 전이하는 경로, 그리고 `request_stop` 경로에서 세 컬럼 null.

## 6. 표면(surfaces) & 원클릭 복구

### 6.1 admin (상세)

- `AgentOut`(`agents.py:198`)에 `unavailable_reason: {code, message, detail} | None` 추가. `_agent_to_out`(`agents.py:253`)가 `render_unavailable_message(..., audience="admin")`로 채움. `detail`(stderr 포함)은 admin 응답에만.
- 렌더:
  - 에이전트 목록 상태 배지 "응답불가" + hover에 사유.
  - AgentSettings State 행(`packages/cluster/frontend/src/components/agent-settings/OverviewPanel.tsx`).
  - 머신 상세 행(`packages/cluster/frontend/src/components/AdminMachines.tsx` — 현재 `last_crash_reason` 미렌더, 사유 렌더 추가).
- **원클릭 복구**: 사유 옆 "재시작/재배치" 버튼 → 기존 `POST /api/v1/agents/{id}/start`(`agents.py:879`)가 `placed_on_machine_id=None` + `desired_state=running` + generation 증가 후 `request_start` 재호출로 재배치까지 수행. 현재 200 무음이므로 **성공/실패 토스트 추가**(`AdminMachines.tsx` start 호출부).

### 6.2 end-user (가벼움)

- **상시**: 사용자 사이드바 에이전트 점/배지(`packages/cluster/frontend/src/components/Sidebar.tsx`)에 offline과 구분되는 "응답불가" 시각 상태 + 짧은 사유 툴팁. WS presence/welcome 프레임에 coarse `available: bool` + 짧은 라벨만 실어 전달(stderr·detail 미포함, `render_unavailable_message(audience="user")`).
- **반응형**: 사용자 메시지가 지명/대상으로 삼은 에이전트의 **`unavailable_code`가 `NULL`이 아니면**(= 미기동이거나 `engine_mismatch`로 running이어도 응답불가), 방에 **시스템 공지** 1건 append. (`actual_state != running` 단독이 아니라 `unavailable_code` 유무를 트리거로 삼아 `engine_mismatch`(running) 케이스도 포함.) 기존 `_ORPHAN_NOTICE_TEXT` 선례(`participant_id=None`으로 append, `lifecycle.py:1111-1119`) 재사용. 문구 예: "⚠️ 에이전트 X는 지금 응답할 수 없습니다 (설정 변경 — 재시작이 필요합니다). 관리자에게 문의하세요."
  - **디바운스**: 방·에이전트·unavailable-window 단위로 1회만(메시지마다 도배 금지). 사유가 클리어된 뒤 재발하면 다시 1회 허용.

## 7. 데이터 플로우 (요약)

1. 전이 지점에서 `Agent.unavailable_*` 기록 + (해당 시) ActivityLog 감사.
2. admin이 REST로 조회 → `AgentOut.unavailable_reason`(상세) → 배지/hover/복구 버튼.
3. 방 join/presence 갱신 시 WS 프레임에 coarse `available`+라벨 → 사용자 사이드바 상시 표시.
4. 사용자 send 시 handler가 대상 에이전트 가용성 확인 → 미가용이면 디바운스 후 방 시스템 공지.
5. admin 원클릭 재시작 or 머신 재광고 → 성공 running 전이 시 사유 클리어 → 모든 표면 정상 복귀.

## 8. 에러 처리 & 엣지 케이스

- **사유 클리어**: 복구 후 stale "응답불가" 방지(§4 클리어 규칙).
- **공지 디바운스**: 방·에이전트·window당 1회. send↔공지 사이에 복구되는 레이스 → 공지 직전 최신 상태 재확인.
- **`desired=stopped`**: 정상 종료이므로 사유·공지·경보 없음.
- **멀티 에이전트 방**: 실제로 응답했어야 할(지명/멘션된) 미가용 에이전트만 공지, 방 전원 아님.
- **권한**: end-user는 coarse 라벨만. `AgentOut.detail`(stderr) 등 상세는 admin 전용(`/agents/*`는 이미 admin-only, WS 사용자 프레임엔 라벨만 실음).
- **engine_mismatch 오탐 방지**: 리포트된 engine이 비어있거나 아직 초기화 전이면 mismatch로 판정하지 않음(명확한 diverge만).

## 9. 테스트 전략

**백엔드 (pytest, `packages/cluster` + `packages/machine`):**
- no_machine except → `unavailable_code="no_machine_for_engine"` + `placed_on_machine_id is None` + ActivityLog 1행.
- spawn 실패 stderr 전파 → `spawn_failed` + `stderr_tail` 담김(machine daemon 단위 + 서버 매핑 단위).
- 성공 running 전이 시 사유 3컬럼 클리어.
- `engine_mismatch` 감지(리포트 engine ≠ DB engine)와 오탐 방지(빈 engine 미판정).
- `render_unavailable_message` audience별 출력(user엔 stderr 미포함).
- 반응형 공지: 미가용 에이전트에 메시지 → 방 시스템 공지 1회 + 디바운스(2번째 메시지엔 미중복).

**프론트엔드 (`packages/cluster/frontend`, `npm run build` 타입체크 + 컴포넌트 테스트):**
- `AgentOut.unavailable_reason` → 배지+hover 렌더.
- 원클릭 재시작이 `POST /agents/{id}/start` 호출 + 성공/실패 토스트.
- 사용자 사이드바가 offline과 구분되는 "응답불가" 상태 렌더.

## 10. 구현 순서 (제안)

1. 데이터 모델 + 마이그레이션 + `render_unavailable_message`.
2. 백엔드 write 경로: no_machine(+placement 리셋) → clear 규칙 → spawn_failed(daemon 전파) → crashed → engine_mismatch.
3. `AgentOut` 확장 + admin 렌더 + 원클릭 복구 토스트.
4. WS 프레임 coarse `available` + 사용자 사이드바 배지.
5. 반응형 방 공지(디바운스).
6. 테스트 전 구간.

## 11. UI 참고

프론트엔드 작업(§6.1, §6.2)은 레포 루트 `DESIGN.md`의 디자인 시스템(warm neutral 팔레트, whisper-weight borders, 단일 accent)을 따른다. "응답불가" 배지는 기존 offline/pending 상태 표기와 시각적으로 구분되되 경보색 남용을 피한다.

## 12. 비목표 (Non-goals)

- hang 감지(능동 헬스 프로브).
- 백그라운드 자동 재시도/자가치유.
- 런타임 엔진 in-place 변경 API 신설(별도 논의). 현재처럼 엔진 변경은 삭제·재생성 또는 마이그레이션 경로 유지.
