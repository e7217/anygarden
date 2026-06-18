# feat(reliability): Wave 2d — CLI-engine LLM telemetry via LifecycleFrame (#461)

- Commit: `71825ca` (71825ca325a0839c649432f2bed7eb23c32a1757)
- Author: Changyong Um
- Date: 2026-06-19T00:33:43+09:00
- PR: #461

## Situation

LLM 사용량 행(`LLMGatewayUsage`)은 gateway 역프록시를 거치는 호출만 기록하는데, 현재 openhands만 gateway 경유라 claude-code/codex/gemini의 LLM 호출은 중앙 텔레메트리·Wave 1d 예산 원장에 안 잡혔다(함대의 CLI 엔진 전부가 관측 사각). 런어웨이 claude-code 루프조차 토큰 행이 안 남았다.

## Task

gateway 라우팅(#359) 없이, 어댑터가 엔진 SDK 결과의 토큰/비용을 `engine_call_finished` LifecycleFrame에 실어(#433 prompt/completion 운반 패턴 재사용) cluster가 usage 행을 기록. 토큰은 항상(비민감), 텍스트는 기존 capture_content 게이트 유지. openhands 이중계산 금지. 마이그레이션 047.

## Action

소스 다수(agent+cluster) + 마이그레이션 047 + 테스트.

- `runtime/handler_wrapper.py` — EngineTurn에 model/input_tokens/output_tokens/cost_usd(기본 None); _run이 engine_call_finished에 전달.
- `protocol/frames.py` + `ws/protocol.py` — LifecycleFrame 동일 옵셔널 필드 미러(패리티).
- `integrations/claude_code.py` — `_extract_result_usage`(ResultMessage.usage input/output + total_cost_usd + model_usage→model), 턴별 stash→EngineTurn. **완전(토큰+cost+model)**.
- `integrations/codex.py` — `run_text`→`run().wait()`(동일 wait_for/timeout, final_text byte-identical)로 CodexTurnStream.usage 접근. `_extract_codex_tokens`. **토큰+model(cost 없음, SDK 1.131.1 검증)**.
- `integrations/gemini_cli.py` — `--output-format json` stats.models[*].tokens 파싱(prompt→input, candidates→output). **토큰+model(cost 없음, 0.39.1 스키마 검증, 방어 파싱)**.
- `db/models.py` — LLMGatewayUsage.cost_usd(nullable Float). 마이그레이션 047_llm_gateway_usage_cost.py(down 046).
- `llm_gateway/reverse_proxy.py` `_write_usage_row` cost_usd 파라미터. `ws/handler.py` `_frame_carries_usage`/`_write_lifecycle_usage_row`: 토큰 또는 model 실은 frame → usage 행(status 200). `api/v1/llm_gateway.py` 집계 cost_usd nullable-safe.

## Decisions

- **LifecycleFrame 운반(gateway 경유 아님)** — #359(gateway DB 모델 등록)는 별 작업. #433 패턴 재사용으로 claude-code 즉시 커버, 서버는 보고된 사실 적재(스위치보드).
- **토큰 항상 기록, 텍스트는 게이트** — 토큰 카운트 비민감(과금/관측 필요), prompt/completion 텍스트만 #433 capture_content 게이트.
- **codex/gemini best-effort(NULL cost)** — 두 SDK 모두 토큰은 노출, cost는 미노출(설치 SDK 검증). 강제 4/4 대신 실제 가용분만, 조작 없음.
- **codex run_text→run().wait()** — usage 접근 위해 필요. 동일 wait_for/timeout 래퍼, final_text가 run_text 결과와 동일(SDK 내부적으로 run().wait().final_text). 테스트 fakes 갱신.
- **openhands 이중계산 회피** — CLI 3종만 토큰 필드 채움, openhands는 all-None → `_frame_carries_usage` False → 행 미기록(gateway 경유분만). 테스트로 고정.

## Result

- agent **455 passed**(+15), cluster **1164 passed**(+9, 독립 재실행), ruff clean. 마이그레이션 047 up/down + head. protocol 패리티 테스트.
- 실제 커버리지: claude-code 토큰+cost+model(완전), codex 토큰+model, gemini 토큰+model — 감사 비관 추정(2/4)보다 양호. openhands 이중계산 회피 테스트 통과.
- 효과: CLI 엔진 LLM 토큰/비용이 중앙 관측·Wave 1d 예산 원장 반영. 런어웨이 claude-code가 토큰 행으로 가시화.
- 후속: codex/gemini gateway 라우팅(#359), lifecycle→Task 재디스패치(request_id↔task 상관 선행 필요 — 별도).
