---
id: 6
title: 신뢰성 하드닝 전략 — 관측에서 복구로 (Wave 0~2)
status: accepted
date: 2026-06-18
---

# 6. 신뢰성 하드닝 전략 (Reliability Hardening)

## Context

paperclip(Node.js "AI 에이전트 회사" 오케스트레이터)과의 비교 분석, 그리고 doorae 8개 신뢰성 영역(스케줄러·turn 실행·에이전트 생명주기·WS 전송·조정/위임·비용/런어웨이·데이터 정합성·관측성) 감사(적대적 검증 포함) 결과, 한 문장으로 요약되는 진단이 나왔다:

> **doorae는 "관측(observability)"을 위해 설계됐지 "복구(recovery)"를 위해 설계되지 않았다.**

turn 결과를 lifecycle 프레임 4종 + OTEL(`parent_request_id` FOLLOWS_FROM)로 정밀하게 *보는* 능력은 뛰어난데, 그 신호로 자동으로 *되돌리는* 메커니즘이 거의 없다. paperclip이 가진 3대 안전망 — **비용 hard-stop / 재시작 복구(reconcile) / 재시도(retry)** — 이 doorae엔 사실상 부재한다. 이 부재가 위험한 이유는 doorae의 강점인 *실시간 멀티에이전트*가 곧 *런어웨이 루프와 turn 유실의 표면적이 넓다*는 뜻이기 때문이다.

감사에서 확인된 결함은 "신호는 있는데 액션이 없는" 패턴이 지배적이다. 대표 예:

- **死안전망**: `goals/sweeper.py`의 execution-timeout 분기는 `Task.started_at`을 요구하는데, 수동 task·`mark_task_status` 경로가 `started_at`을 채우지 않아 사실상 동작하지 않는다.
- **무성 유실**: 한 룸이 turn 처리 중일 때 도착한 두 번째 메시지는 `outcome=rejected`로 *사용자 통지 없이* 버려진다. gemini 비정상 종료는 `return None`으로 무성 처리된다. 에이전트 크래시 중 in-flight turn은 1200초 orphan sweeper가 메트릭만 올릴 뿐 통지/복구가 없다.
- **비용 무방비**: 토큰·비용 hard-stop이 코드 어디에도 없다(`token_stats.py`는 추정·수집만). cycle_guard는 *동일 내용* 루프만 잡는다.
- **스케줄러 단일 레플리카**: goal 스케줄러는 CAS/멱등성 없는 in-process 폴링이라 멀티레플리카·Run-now·재시작 시 중복발사 또는 유실 위험. 재시작이 healthy goal을 `consecutive_failures=3`로 auto-pause시킨다.
- **재시작 복구 부재**: 데몬 재시작 시 살아있는 자식 프로세스를 re-adopt하지 않아 2N 프로세스(중복 응답 + 죽일 수 없는 좀비 + 토큰 이중 소모)가 생긴다.

핵심 설계 제약: doorae의 정체성인 **"서버는 스위치보드, not 브레인"**([04-orchestration](../design/04-orchestration.md))과 **"LLM 판단 최소화"**([003-delegation-orchestration-strategy](./003-delegation-orchestration-strategy.md))를 깨지 않아야 한다. 신뢰성 보강이 서버를 "판단하는 두뇌"로 만들면 안 된다.

## Decision

신뢰성을 **웨이브 단위로 점진적으로** 하드닝한다. 각 보강은 새 판단(LLM 호출·turn 결정)을 추가하지 않고, *타임스탬프 기록 / 정수 비교 / 예외 전파 / 원자적 행 잠금* 같은 결정론적 메커니즘만 쓴다 — 스위치보드 철학 유지.

### Wave 0 — 죽은 안전망 부활 + 무성 유실 차단 (본 PR, #445)

전부 S 난이도·저위험·마이그레이션 없음. "이미 깔려 있으나 작동하지 않는 안전망을 되살리고, turn이 침묵 속에 사라지는 경로를 막기".

1. `Task.started_at/finished_at` 상태전이 스탬프 → exec-timeout sweeper 부활
2. gemini 비정상 종료 `raise EngineError` (무성 None 제거)
3. rejected turn 사용자 통지 (timeout/failed와 대칭)
4. typing-ping await (3개 어댑터, 멈춘 "입력 중" 제거)
5. goal API UTC 타임존 수정 (비-UTC 호스트 드리프트)
6. `/healthz` 실제 의존성 체크 (DB/gateway/백그라운드태스크)
7. WS replay 50개 캡 제거 (커서 페이지네이션)
8. WS seq dedup (재접속 중복 디스패치 방지)
9. WS 재접속 jitter + 4040 처리
10. handler 리스트 스냅샷 순회 + `get_running_loop`
11. anygarden 토큰 커밋 후 캐싱 (재시작 401 storm)
12. room_query per-sender 키잉

### Wave 1 — 핵심 안전망 (별도 이슈/PR, M 위주, 마이그레이션 동반)

- **비용 원장 + invocation-block**: 이미 존재하는 per-call 비용 스트림 `LLMGatewayUsage` 위에 `token_budget_policies`(scope=global/agent/room) + gateway reverse proxy chokepoint에서 `observed >= ceiling` 시 429 거부. agent·global은 무조건, room은 best-effort(room_id가 pre-call에 불확실). `hard_stop_enabled` 기본 OFF. — *결정: 새 비용 추정기를 만들지 않고 실측 usage 행을 합산(정수 비교만).*
- **goal CAS claim + 멱등성**: `_tick`을 `UPDATE ... WHERE next_run_at<=now RETURNING`(원자적 claim)으로, `Task.idempotency_key`(goal+슬롯) unique. → 슬롯당 정확히 1회 발사, 멀티레플리카 correct-by-construction(분산 락 불필요). *주의: `trigger_goal`의 next_run_at 이중 advance를 claim 안으로 이동.*
- **goal per-tick cap + in-flight dedup + 부팅 reconcile**.
- **데몬 재시작 시 에이전트 re-adopt** (`runtime.json` + `load_all_running`).
- **에이전트 heartbeat reaper** (`last_heartbeat_at` 임계 초과 + 머신 offline → `crashed`).
- **ActivityLog `outcome`/`engine` 인덱스 컬럼** (turn 실패 쿼리 가능화, #427 패턴).

### Wave 2 — 복구 심화 (L, 기능 플래그 뒤)

- 비용 active-stop(`request_stop`로 런어웨이 에이전트 정지, agent 스코프만), bounded 룸 큐(rejected→defer), lifecycle→Task 재디스패치 브리지, transient 재시도(default-OFF), task_blockers 의존성 관계, CLI 엔진 LLM 텔레메트리.

### 하지 않기로 한 것 (안티패턴)

- **머신 staleness sweep**(현 상태) — `daemon_last_seen_at`이 register 때만 기록돼 살아있는 함대를 offline 처리. 선행작업(매 프레임 갱신) 필요.
- **`max_agents` 기본값 변경 / 메모리 헤드룸 가드** — `max_agents=1000`은 의도된 제품 결정(#2). 에이전트별 메모리 추정 신뢰 불가.
- **`/delegate` 프리픽스에 id 삽입** — 3곳 `startswith` 깨짐. id는 metadata로.
- **rejected의 클러스터측 system-message inject** — `inject_system_message` 헬퍼 부재, NULL-participant 고아 버블 렌더. 에이전트측 `client.send`가 최소 정답.

## Consequences

**효과**: 무성 turn 유실 경로 제거 · exec-timeout 안전망 0→full · 재시작 401 storm 종료 · WS gap>50 무성손실 제거(Wave 0). 비용 무한지출 → ceiling 자가차단 · goal 정확히-1회 · 재시작 중복응답 0 · 죽은 에이전트 MTTD 20분→120초(Wave 1).

**비용/리스크**: Wave 0는 마이그레이션 없음·저위험(예외: e2e healthz 단언 1곳 갱신). Wave 1+는 마이그레이션·새 테이블·게이트 동반 → 각 항목 기능 플래그 + 보수적 기본값(예: 비용 hard-stop 기본 OFF)으로 점진 도입. `Task.started_at` 부활로 600초 넘는 정상 long task가 오탐될 수 있어 타임아웃 정책은 후속 조정 여지를 둔다.

**철학 보존 확인**: 모든 보강은 결정론(타임스탬프·정수 비교·행 잠금·예외 전파)이며 서버가 LLM을 부르거나 turn을 결정하지 않는다 — 스위치보드 불변.

## 참고
- 비교/감사 근거: paperclip(paperclipai/paperclip) 비교 분석 + 8개 영역 신뢰성 감사(적대적 검증)
- 관련: [001](./001-engine-subprocess.md), [003](./003-delegation-orchestration-strategy.md), [004](./004-embedded-litellm-gateway.md)
- 이슈: #445(Wave 0). Wave 1·2는 후속 이슈로 분할.
