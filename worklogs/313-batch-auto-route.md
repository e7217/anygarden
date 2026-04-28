# feat(rooms): batch auto-route unassigned tasks via room representative (#313)

- Date: 2026-04-28
- PR: TBD (from `feat/313-batch-auto-route`)
- Stacked on: PR #312 (`feat/312-auto-rep-and-assignee-picker`, #315)

## Situation

PR #312 가 우측 사이드바 TasksSection 의 직접 배정 picker 를 복원했지만, 사용자가 "여러 task 를 만들어두고 누가 뭘 할지 나중에 정리" 하는 운영 패턴은 여전히 손이 갔다. 매번 picker 로 한 건씩 + description 적합도 사람이 판단 = 비용 큼. PR #312 의 rep invariant ("빈 룸 외에는 항상 rep 존재") 가 보장됐으니 rep 에이전트에게 일괄 라우팅을 맡길 수 있는 토대가 마련됨.

## Task

- 우측 사이드바 TasksSection 헤더에 🪄 버튼 추가
- POST `/api/v1/rooms/{id}/auto-route-unassigned` — unassigned task 수집 + rep 에 라우팅 요청 + 응답 파싱 + 일괄 배정 + WS fanout
- rep 에이전트의 *실제 추론 체인* 사용 (D1=B) — cluster 가 LiteLLM 직접 호출 X. 이유: LiteLLM 게이트웨이가 현재 정상 동작 안 하는 상태 (사용자 명시) + rep 의 메모리/skills 가 미래에 가치 있음
- 마커 기반 in-band 프로토콜 — 에이전트 SDK 의 `EngineAdapter` 추상이 `decide_policy → on_message` 공통 처리하므로 codex/gemini/claude 코드 변경 0
- 30s timeout, rep offline 시 422 + UI 힌트
- 부분 응답 / 형식 오류 robust 파싱
- 사용자가 보는 chat 에는 프로토콜 에코 노출 안 함 (`system_origin` 마커로 frontend 필터)

## Action

### Stage 1 — Routing 프로토콜 (Phase A)

- `packages/cluster/doorae/routing/__init__.py` (+50 lines, new) — 모듈 docstring + re-export
- `packages/cluster/doorae/routing/protocol.py` (+200 lines, new):
  - 마커 상수: `[DOORAE_ROUTING_REQUEST` / `[DOORAE_ROUTING_RESPONSE` (id=<uuid>] 형식)
  - dataclass: `_AgentLine`, `_TaskLine`, `RoutingResult`
  - `format_routing_prompt(request_id, room_name, agents, tasks)` — 마커 + 룸명 + 에이전트 description list + task list + 응답 형식 spec 을 markdown 으로 조합. 빈 description / 멀티라인 description 처리.
  - `parse_routing_response(request_id, content)` — id 매칭 검증 + 코드펜스 unwrap + JSON 객체 검증 + 값 string 타입 검증. 실패 시 `RoutingResult.fail(error)` 반환 (raise 하지 않음 — 부분 응답 처리 위해).
  - `try_parse_routing_response(content)` — request_id 사전 지식 없이 inbound 메시지에서 마커 추출. WS 훅에서 사용.
- `packages/cluster/tests/test_routing_protocol.py` (+165 lines, new, 14 tests):
  - 프롬프트 포맷팅 3 케이스 (request_id 포함, 빈 description, 멀티라인 collapse)
  - 응답 파싱 8 케이스 (pure JSON / 코드펜스 / 앞 prose / id 불일치 / 마커 없음 / invalid JSON / array payload / 값 비-string)
  - try_parse 3 케이스 (마커 없으면 None / id 추출 정상 / 페이로드 깨져도 id 는 추출)

### Stage 2 — API + WS 훅 (Phase B)

- `packages/cluster/doorae/routing/router.py` (+250 lines, new):
  - `POST /api/v1/rooms/{room_id}/auto-route-unassigned` — Pydantic `AutoRouteResult` (routed[], skipped[], rep_agent_id, request_id) 응답
  - 흐름: 룸/rep 검증 → 후보 에이전트 수집 → 빈 unassigned 면 즉시 빈 결과 반환 → request_id 생성 + Future 등록 → 마커 메시지 inject + 룸 broadcast → `asyncio.wait_for(fut, 30)` → 결과 파싱 → 각 매핑별 task assignee 채우기 + `inject_task_assignment_message` (#266 재사용) → fanout 'reassigned' 이벤트
  - 에지: rep NULL → 422 / rep `actual_state != running` → 422 with hint "Start it first" / timeout → 504 / 매핑 응답 깨짐 → 502 / 매핑 후 task 가 사라짐 또는 룸 비참여 agent 면 skipped 처리
  - rep 응답에 빠진 task → orphan reason 으로 skipped 에 추가 (사용자가 "2/3 routed" 명확히 인지)
- `packages/cluster/doorae/ws/handler.py` (+25 lines):
  - inbound 메시지 처리에서 `identity.kind == "agent"` 시 `try_parse_routing_response(content)` 호출
  - 매칭 시: `app.state.routing_futures` 에서 Future pop + `set_result`. metadata 에 `system_origin = 'auto_route_response'` + `routing_request_id` 추가 (frontend 가 hide).
  - append_message 직전에 훅 → 메시지가 올바른 metadata 와 함께 영속됨 (감사 추적 보존)
- `packages/cluster/doorae/app.py` — `routing_router` 등록.
- 검증: cluster 875 → 889 tests green (14 new).

### Stage 3 — Frontend 🪄 (Phase C)

- `packages/cluster/frontend/src/lib/routing.ts` (+45 lines, new) — `autoRouteUnassigned(roomId)` REST 헬퍼. 422/502/504 detail 을 throw 메시지로 surface.
- `packages/cluster/frontend/src/components/right-rail/TasksSection.tsx` (+60 lines):
  - 헤더에 Wand2 버튼 (Loader2 spinner 시 swap). `unassignedCount` useMemo (status≠done + assignee=null 만 카운트).
  - `handleAutoRoute` — POST → routedNames count + skipped 합산 → 4초 inline toast (`role="status"`) → refresh.
  - 결과 toast 형태: "Routed: 2 → emma, 1 → noah (1 skipped)"
- `packages/cluster/frontend/src/components/ChatArea.tsx` (+12 lines):
  - 메시지 렌더 루프에서 `metadata.system_origin in {auto_route_request, auto_route_response}` 면 null 반환. 영속 row 는 그대로 (감사용) — 렌더만 hide.

### Stage 4 — 검증

- `cd packages/cluster && uv run --with pytest --with pytest-asyncio python -m pytest tests/` → 889 passed (875 + 14 new), 1 deselected, 1 warning.
- `cd packages/cluster/frontend && npm run build` → 9.71s clean (tsc 포함).
- `npx vitest run` → 37 files, 375 tests (회귀 zero).

## Result

- **🪄 한 클릭 일괄 라우팅** — 사용자가 unassigned task 들을 만들어두고 헤더 버튼 한 번 → rep 가 description 기반으로 적합 에이전트에게 분배 + 각 assignee 가 #266 inject 경로로 깨어남. 4초 toast 로 결과 즉시 인지.
- **rep 의 실제 추론 체인 활용** — cluster 가 LLM 직접 호출하지 않음. rep agent 가 자기 컨텍스트(메모리/skills/이전 대화) 위에서 결정. 향후 rep 의 결정 품질이 단순 description 매칭보다 우월해질 잠재력 보존.
- **엔진별 코드 변경 0** — `EngineAdapter` 추상 (#293) 덕분에 마커 인식 + 응답 emit 이 모든 어댑터 자동 적용. codex/gemini/claude 어느 파일도 안 건드림.
- **회귀 zero** — 백엔드 889 + 프론트 375 모두 그대로 green. 신규 코드 없는 채팅 흐름 + 기존 task assignee 로직 보존.
- **신규 코드량** — 약 800 라인 추가 (백엔드 protocol 200 + router 250 + WS hook 25 + 테스트 165 / 프론트엔드 lib 45 + UI 60 + ChatArea 12 + worklog). 마이그레이션 0건, 신규 dep 0건.
- **사용자가 보는 결과** — chat 스레드는 깨끗 (프로토콜 에코 hide), 룸의 task 행은 새 assignee + assignment 멘션 메시지가 보임 (이건 정상 task 이벤트라 보존).

## TODO (별도 이슈로 분리 권장)

- **LiteLLM 게이트웨이 fallback 라우터** — 게이트웨이가 정상화되면 rep offline 케이스에 사용. `app.state.routing_futures` 와 `parse_routing_response` 가 재사용 가능하므로 API 계약 / UI 변경 0.
- **MCP `create_task` (#270) 통합** — 에이전트가 생성한 task 도 같은 라우팅 서비스로. agent-side 와 user-side 가 동일 진실.
- **rep 의 self-bias 측정** — rep 가 description 무관하게 자기 자신만 픽하는 경향이 운영 데이터에서 확인되면 prompt 에 "prefer specialists" nudge 추가.
- **description 미작성 유도 토스트** — 모든 에이전트가 description 비어 있으면 라우팅이 이름만 보고 결정. AgentSettingsDialog 에서 description 작성 권유.
- **재라우팅 버튼** — 사용자가 LLM 결과에 만족 못 하면 재시도. 같은 엔드포인트 재호출만 하면 됨 (rep 가 다른 답을 줄 수 있음).
