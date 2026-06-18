# feat(reliability): Wave 1d — token cost ledger + invocation-block gate (default OFF) (#453)

- Commit: `11eddd3` (11eddd3221f799749ae3a39a0db76907e3a1cdb8)
- Author: Changyong Um
- Date: 2026-06-18T22:22:27+09:00
- PR: #453

## Situation

ADR-006 Wave 1의 비용 안전 조각이자 감사의 **유일한 critical**. doorae엔 토큰/비용 hard-stop이 전무해, 런어웨이·버그·크래시루프 에이전트가 사람이 눈치챌 때까지 무한 지출할 수 있었다(`token_stats.py`는 추정·수집만, 강제 0건). 다만 LLM 호출당 1행을 쌓는 실측 스트림 `LLMGatewayUsage`가 이미 존재했다.

## Task

한 PR(마이그레이션 044)로, 스위치보드/결정론 유지 + **최고위험이라 기본 OFF**(병합이 런타임 동작을 바꾸지 않아야 함):
- 정책 테이블 + 실측 토큰 원장 + gateway chokepoint invocation-block(429)
- 비협조 에이전트도 봉쇄(서버 게이트)
- DB 오류가 LLM 트래픽을 죽이지 않게(fail-open)
- active-stop/incidents/cost(USD) 컬럼은 Wave 2

## Action

소스 5(신규 3) + 마이그레이션 044 + 테스트 3(29) +.

- `db/models.py` — `TokenBudgetPolicy`(token_budget_policies): scope_type(global|agent|room), scope_id, token_ceiling, warn_percent(80), window_kind(rolling_24h), `hard_stop_enabled`(기본 **False**), is_active. 인덱스 (scope_type,scope_id,is_active).
- `db/migrations/versions/044_token_budget_policies.py`(down 043) — create_table + 인덱스 + `ix_llm_gateway_usage_room_ts`(room_id,timestamp, 부재했음). up/down 라운드트립 검증.
- `budgets/ledger.py`(신규) — `compute_observed_tokens`(SUM(coalesce(prompt,0)+coalesce(completion,0)) WHERE status_code<400 AND scope 매칭) + `InvocationBlock` + `evaluate_invocation_block`(global/agent/room 우선순위, is_active AND hard_stop_enabled 필터) + 8s TTL 캐시(clear_observed_cache 테스트 훅).
- `llm_gateway/reverse_proxy.py` — `client.request` 전 게이트: room_id_hint = tracing._correlate(agent_id).room_id(try/except, best-effort), evaluate_invocation_block(try/except **fail-open**), 차단 시 429(Retry-After:60)+JSON error + status_code=429 usage 행(background).
- `api/v1/budgets.py`(신규) — admin CRUD(get_admin_identity), app.py include. head 가드 043→044.

## Decisions

- **실측 LLMGatewayUsage SUM**(token_stats 추정 아님) — docstring이 추정을 "non-billing"으로 명시. LiteLLM spend log 비활성이라 토큰이 유일 proxy.
- **gateway chokepoint 게이트** — 모든 LLM 호출의 유일 지점이고 이미 identity 게이트 존재 → 최소 삽입. **비협조 에이전트도 우회 불가**(에이전트측 가드는 우회됨).
- **기본 hard_stop_enabled=False** — 미튜닝 ceiling으로 켜면 정상 heavy 작업을 429로 막아 사고. 기본 OFF면 정책 0개=no-op이라 병합 무위험; 운영자가 스코프별 신중 활성화.
- **fail-open** — budget 평가를 try/except로 감싸 DB 오류 시 block=None. 비용 보호 기능이 LLM 트래픽 자체를 죽이는 일은 절대 없어야 함(가용성 > 비용보호).
- **status_code<400 필터(필수)** — 게이트가 쓰는 429 거부행이 observed를 부풀려 영구 차단(self-perpetuating)되는 것 방지.
- **room best-effort** — room_id는 pre-call에 불확실(상관은 post-call). agent+global 확정, room은 tracing in-memory로 알 때만. 강제하려다 tracing off 시 silently 미발효되는 혼란 회피.
- 시드 생략(계획 허용) — fresh DB 정책 0개가 default-OFF 불변을 가장 깔끔히 증명.

## Result

- cluster **1123 passed**(독립 재실행), ruff clean. 마이그레이션 044 up/down + room 인덱스 검증.
- **기본 OFF 무변경 불변 증명**: 정책 없이 1천만 토큰 누적해도 proxy가 200 반환·업스트림 호출(test_default_no_policy_passes_through); 비활성/warn-only 정책도 통과. ledger도 정책 없음/비활성/hard_stop_off에서 None.
- 효과: 유한·쿼리가능 token ceiling 최초 도입. 정책을 켜면 비협조 에이전트까지 서버에서 429 차단. fail-open으로 가용성 보존.
- 후속(Wave 2): active-stop(evaluate_cost_event→request_stop), token_budget_incidents, Agent.pause_reason, bounded 룸 큐, task_blockers, CLI telemetry.
