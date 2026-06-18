# feat(reliability): Wave 2a — budget active-stop (halt runaway agents + incidents) (#455)

- Commit: `7d0fee8` (7d0fee8929189933a2f48c4772b6ab3fc0180585)
- Author: Changyong Um
- Date: 2026-06-18T22:46:03+09:00
- PR: #455

## Situation

Wave 1d(#453)의 invocation-block은 ceiling 초과 시 다음 LLM 호출을 429로 거부(새 지출 차단)하지만, 런어웨이 에이전트는 매 poll/turn마다 429를 받으며 계속 스핀한다 — 실제 *정지*가 없으면 루프가 멈추지 않는다. paperclip의 post-cost evaluateCostEvent + pauseAndCancelScopeForBudget에 해당하는 절반이 doorae엔 없었다.

## Task

Wave 1d 위에, 기본 OFF를 상속(활성 hard_stop 정책이 있어야 발동)한 채: 성공 usage 기록 후 예산을 평가해 AGENT 스코프 hard 초과 시 그 에이전트를 실제 정지하고 incident를 남긴다. room/global은 부수피해 방지로 incident-only. 마이그레이션 045.

## Action

소스 6 + 마이그레이션 045 + 테스트 3(17).

- `db/models.py` — `Agent.pause_reason`(nullable String(32)); 신규 `TokenBudgetIncident`(policy_id, scope, window_start, threshold_type soft|hard, status open|resolved, observed_tokens) + 인덱스 (policy_id,status)/(scope_type,scope_id).
- `db/migrations/versions/045_token_budget_incidents.py`(down 044) — add_column(agents.pause_reason) batch + create_table. up/down 검증.
- `budgets/ledger.py` — `evaluate_cost_event(session_factory, *, agent_id, room_id, lifecycle)`: 활성 hard_stop 정책별 fresh SUM(캐시 미사용, 방금 행 반영); hard→`_ensure_open_incident`(dedup) + AGENT scope만 `lifecycle.request_stop` + pause_reason='budget'(eval tx 커밋 후 발행), room/global incident-only; soft→soft incident; try/except 방어(post-response). `evaluate_invocation_block`에 pause_reason=='budget' short-circuit.
- `llm_gateway/reverse_proxy.py` — 성공 경로(SSE + 비스트리밍) 양쪽 usage 기록 뒤 evaluate_cost_event 체이닝. 에러/502/429 경로 미연결.
- `api/v1/budgets.py` — POST resume(admin): pause_reason clear + open agent incident resolved + request_start.
- `observability/metrics.py` — Counter budget_incidents_total{threshold}, agents_stopped_by_budget_total. head 가드 044→045.

## Decisions

- **AGENT만 auto-stop, room/global incident-only** — room/global hard에 전 방/함대를 죽이면 무고한 작업 부수피해. agent 스코프만 책임이 명확. 운영자가 incident로 판단.
- **request_stop 재사용** — desired_state=stopped→머신 subprocess kill이 pause+cancel을 원자적으로 수행. 새 경로 불필요.
- **incident dedup = (policy_id, threshold_type, status='open')** (계획의 window_start 정확매칭에서 변경) — rolling_24h window_start가 호출마다 수초씩 이동해 정확매칭이면 매 초과 호출마다 새 incident 폭증. open incident 하나로 묶고 resume이 resolve.
- **fresh SUM(캐시 미사용)** — 정지 결정은 방금 쓴 행을 봐야 함. 캐시는 invocation-block hot path 전용.
- **stop은 eval tx 커밋 후** — pause_reason 플립이 request_stop의 별도 세션에 보이도록.
- **resume=request_start 경유** — placement/sync 정상 재기동.
- 기본 OFF 상속: 활성 hard_stop 정책 0개면 정책 쿼리 0행 → 무동작.

## Result

- cluster **1140 passed**(+17, 독립 재실행), ruff clean. 마이그레이션 045 up/down + head 검증.
- 기본 OFF 무변경 증명: 정책 없이 1천만 토큰·proxy 성공 호출에도 incident·stop·pause_reason 없음. agent hard→stop+incident, room/global hard→incident만, soft→soft incident, 멱등(중복→1 incident, re-stop no-op) 검증.
- 효과: 런어웨이 에이전트가 ceiling 교차 후 1 sync 주기 내 자동 정지(현재 사람이 끌 때까지 무한). fail-safe·기본 OFF로 안전.
- 후속(Wave 2b+): bounded 룸 큐, transient 재시도, lifecycle→Task bridge, task_blockers, CLI telemetry.
