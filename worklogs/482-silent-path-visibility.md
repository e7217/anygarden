# fix(agent): silent-drop 경로 가시화 — request_id=None 빈응답·cycle SKIP·client 예외/한도 (#482)

- Commit: `c16d703` (c16d703 on branch fix/482-silent-path-visibility)
- Author: Changyong Um
- Date: 2026-06-22
- Issue: #482

## Situation

#422가 사용자-트리거 턴(`request_id` 있음)의 빈 응답을 `failed`로 surface하게
만들었지만, 에이전트 내부에는 여전히 **사용자·운영자에게 전혀 안 보인 채 조용히
종료되는** 경로들이 남아 있었다:

1. agent→agent 비-nominated peer 멘션의 빈 응답 — `request_id=None`이라
   `handler_wrapper.py`의 empty→failed 재분류 가드(`request_id is not None`)를
   통과하지 못해 항상 `outcome=ok`로 남고 어디에도 신호가 없었다.
2. `decide_policy` 의미적 사이클 SKIP — `base.py`에서 `decide_policy.cycle_detected`
   warning 로그만 남기고 SKIP(메트릭/카운트 미연동).
3. client 레벨 silent return — `client.py`의 `max_agent_turns` 초과 `return`(info
   로그만), 핸들러 예외 삼킴(error 로그만).

이 세 경로 모두 "정상 흐름처럼 읽히는" 로그 한 줄만 남겨, 운영자가 "무응답이
*언제* 일어나는지"를 측정할 수 없었다.

## Task

- 위 세 경로에 관측 가능한 신호를 추가하되 **기존 silent-ok 룸 동작·outcome은
  불변**(backward-compatible)으로 유지한다.
- (1)의 비-nominated 빈응답을 `failed`로 재분류하면 "의도된 무응답"까지 거짓
  실패로 룸에 스팸하게 되므로(계약 파괴), 구분 불가 영역은 **관측성만** 더한다.
- 제약: `integrations/claude_code.py`·`openhands_engine.py`는 다른 PR(#483) 소관이라
  건드리지 않는다.

## Action

- `observability/metrics.py`(신규) — agent 프로세스는 cluster와 달리
  Prometheus 레지스트리·`/metrics` 노출이 **없다**(의존성 `prometheus-client`도
  미보유). 그래서 의존성을 새로 끌어오는 대신, `inc()`마다 in-process 정수를
  올리고 동시에 `metrics.counter_inc` structlog 이벤트를 남기는 가벼운 `_Counter`
  shim을 추가했다. `value()`는 단위 테스트로 검증 가능하고, structlog 이벤트는
  운영 로그의 breadcrumb가 된다. 카운터 4종: `agent_empty_untracked_total`,
  `decide_policy_cycle_skip_total`, `agent_turn_limit_skip_total`,
  `client_handler_error_total`(전부 라벨 없음 — 카디널리티 비이슈).
- `runtime/handler_wrapper.py` — `outcome=="ok" and not response and
  request_id is None` 분기를 추가(기존 tracked-empty→failed 분기 바로 뒤 `elif`).
  outcome은 ok로 유지하고 룸 전송도 안 하되, `engine_call_finished` 프레임의
  `error`를 `"no_response(untracked)"` 센티넬로 채우고
  `agent_empty_untracked_total`을 inc.
- `integrations/base.py` — `decide_policy`의 cycle SKIP 분기에 기존 warning 로그
  유지 + `decide_policy_cycle_skip_total.inc()`.
- `client.py` — `max_agent_turns` 초과 drop에 `agent_turn_limit_skip_total.inc()`,
  그리고 env gate `ANYGARDEN_SURFACE_SILENT_PATHS`(truthy일 때만)로 룸 시스템
  라인 1줄을 best-effort 전송(기본 off → 룸 무전송; 전송 실패는 debug 로그로
  삼킴). env는 사용 지점에서 `os.environ.get` 인라인 + `_is_truthy` 헬퍼.
  핸들러 예외 삼킴 분기에 `client_handler_error_total.inc()`(로그·계속 동작 불변).
- 테스트: `test_observability_metrics.py`(신규, 카운터 inc/value/reset/structlog
  이벤트), `test_handler_supervisor.py`(untracked-empty가 silent-ok 유지 +
  센티넬 + 카운터, non-empty는 무마킹/무카운트), `test_should_respond.py`(cycle
  SKIP 카운터), `test_client.py`(turn-limit 카운터 + gate on/off 룸 라인,
  핸들러 예외 카운터 + 후속 핸들러 계속 실행).

## Decisions

- **C(error 센티넬 + 카운터, 관측성만) 채택**, A(빈응답을 outcome=failed로
  재분류)·B(새 outcome 값 추가) 기각 — `.tmp/plan-482-silent-path-visibility.md` §3.2.
  - 비-nominated 빈응답의 "의도된 no-reply vs 실패"는 supervisor가 구분 불가
    (`test_proactive_empty_response_stays_silent_ok`가 silent-ok를 명시 보장).
    룸 동작을 바꾸면 거짓 실패가 나가므로 구분 불가 영역은 관측성만 더하는 게 안전.
- **Prometheus 대신 in-process 카운터+structlog 채택** — agent에 레지스트리/노출
  엔드포인트가 없어 진짜 Counter를 달아도 scrape 불가. 계획이 명시한
  "레지스트리 없으면 lifecycle/로그 기반 대체"를 따른 것.
- **계획 §3.1의 ActivityLog 질의 가정은 부분 정정**: 계획은 센티넬을 실은
  `engine_call_finished` 프레임이 cluster `_lifecycle_details`로 영속돼
  ActivityLog 질의가 된다고 가정했으나, 실제 `ChatClient.sendLifecycle`은
  `request_id is None`일 때 **early-return으로 no-op**한다(client.py:333). 따라서
  untracked-empty 프레임은 production에서 cluster로 전송되지 않으며, 이 경로의
  **실효 신호는 in-process 카운터 + `metrics.counter_inc` structlog 이벤트**다.
  프레임의 센티넬은 룸/전송 동작에 무해(additive)하며, 향후 untracked 턴이
  lifecycle-tracked로 바뀌거나 프레임 전송 직전 검사 도구가 보는 경우의 올바른
  값으로 남겨 둔다.
- **메트릭 모듈 위치**는 cluster의 `observability/metrics.py`와 대칭으로
  agent에도 `observability/metrics.py`를 신설.

## Result

- `uv run pytest packages/agent` → 466 passed(기존 387 → 신규 테스트 추가).
  `test_proactive_empty_response_stays_silent_ok` 포함 기존 계약 테스트 전부 green.
- `uv run ruff check packages/agent` → 변경/신규 파일 전부 clean. 레포 전반의
  기존 8건(F401/F821, 모두 본 변경과 무관한 기존 테스트/import lint 부채; 격리된
  worktree HEAD 기준 동일 8건)은 #482 범위 밖이라 그대로 둠.
- 카운터 4종 모두 `ALL_SILENT_PATH_COUNTERS` 레지스트리에 등록(test reset/
  향후 집계용 단일 출처).
- 룸 표면화는 기본 off(env gate). cluster/wire 프로토콜 변경 없음.
- Pending/범위 밖: untracked-empty의 cluster-side 영속(현재 no-op 경로라 별도
  설계 필요), `integrations/claude_code.py`·`openhands_engine.py`의 동형 처리(#483).
