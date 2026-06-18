# feat(reliability): Wave 2b — bounded room queue (defer) + transient retry (default OFF) (#457)

- Commit: `3cb7d41` (3cb7d41ed4ad10153b3b327c4dc456f28c8e5081)
- Author: Changyong Um
- Date: 2026-06-18T23:08:14+09:00
- PR: #457

## Situation

Wave 0은 처리 중 도착한 후속 메시지를 `rejected`로 드롭하고 통지만 했다(유실은 그대로). 또한 transient 게이트웨이/네트워크 장애(429/5xx/conn-reset)에 대한 재시도가 전혀 없어 일시 장애가 곧 하드 실패였다.

## Task

agent 패키지 + 양 패키지 protocol Literal로: (1) rejected를 bounded FIFO 큐 defer로 승격(룸당 1 turn 직렬화 유지), (2) transient 재시도(기본 OFF, 무출력만). 두 변경이 같은 outcome Literal을 건드리므로 한 PR로 묶어 프로토콜 마이그레이션 1회.

## Action

9 파일 +772/-100.

- `protocol/frames.py` + `ws/protocol.py` — `outcome` Literal에 queued/retrying/retry_exhausted 동시 추가(event 불변, test_protocol_compat 패리티).
- `runtime/handler_wrapper.py` — `dispatch`: 락 free면 acquire→`_run`→**락 보유한 채** `_drain_queue`(레이싱 dispatch는 계속 lock.locked()→enqueue → 룸당 정확히 1 turn). 락 점유 중이면 큐<cap(기본 3, ANYGARDEN_ROOM_QUEUE_DEPTH) push+outcome=queued, cap 초과만 rejected+통지(Wave 0). `_drain_queue` FIFO popleft, TTL(기본 60s) 초과 stale skip+통지. `EngineError.__init__(*,transient=False)` + `is_transient_error()`(429/500/502/503/504 토큰 + rate limit/overloaded/conn reset 등). `_run` retry 루프: timeout|failed + transient + 무출력 + attempt<max → 백오프(2→8s) 재invoke, 소진 시 retry_exhausted. `ANYGARDEN_TURN_MAX_RETRY_ATTEMPTS` 기본 0(분기 unreachable). cancelled 비대상.
- `integrations/{gemini_cli,codex,claude_code,openhands_engine}.py` — raise 지점 최소 transient 분류.
- `tests/test_handler_supervisor.py`, `tests/test_protocol_compat.py` — 큐/직렬화/TTL/retry/분류/패리티 테스트.

## Decisions

- **작은 bounded FIFO + TTL(드롭 대신 defer)** — 무제한 큐는 메모리·stale 답 위험, 드롭 유지는 유실. cap 3 + TTL 60s가 빠른 후속(DM 연타·HANDOFF)을 살리되 폭주/지각답 방지. cap 초과는 여전히 rejected.
- **drain을 락 보유한 채 수행** — 레이싱 dispatch가 계속 enqueue하도록 해 룸당 1 turn(직렬화 불변)을 보장. test_serialization_invariant가 max-concurrency 1 단언.
- **queued/retrying을 outcome(event 아님)** — handler_finished의 결과값일 뿐. event Literal 불변이라 trace/metric 흐름 유지, 양 패키지 동시 bump.
- **transient 재시도 기본 OFF + 무출력만** — 재invoke는 출력 전 부작용 재실행 위험. 무출력 가드 + 옵트인이 안전. 기본 0이면 분기 unreachable → 동작 무변경.
- 분류 최소(기본 OFF라 빈 분류여도 안전, 옵트인 시 점진 확대).

## Result

- agent **440 passed**, cluster **1140 passed**, protocol-compat 13, supervisor 34(독립 재실행). ruff 신규 에러 0.
- 직렬화 불변(max-concurrency 1)·기본 OFF 무재시도(attempt<0 unreachable)·무출력 가드·TTL skip·cap 초과 rejected 검증.
- 효과: 빠른 후속 메시지가 유실 대신 순서대로 처리(rejected 급감); 옵트인 시 transient 장애 자가 복구.
- 알려진 trace 뉘앙스(후속): queued/retrying이 handler_finished로 방출돼 retried/queued turn의 cluster trace가 조기 finish(방어적 no-op, 크래시 없음, 옵트인/큐 경로만). 향후 non-terminal event로 개선 여지.
- 후속(Wave 2c+): task_blockers, CLI telemetry, lifecycle→Task 재디스패치.
