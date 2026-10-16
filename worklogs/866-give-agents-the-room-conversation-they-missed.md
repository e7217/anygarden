# fix(agents): give agents the room conversation they missed (#866)

- Commit: `05c794d` (05c794df932f98b367e12aa13609f59aab47fb22)
- Author: Changyong Um
- Date: 2026-10-16T16:20:23+09:00
- PR: #866

## Situation

에이전트가 멘션으로 깨어났을 때 직전에 룸에서 오간 대화를 모르는 경우가 잦았다.
세 가지 경로로 문제가 발생했다: (1) `decide_policy` 규칙 5가 자신이 아닌 다른
에이전트에게 보낸 메시지를 통째로 버려, 세 번째 에이전트가 동료의 답변은 보지만
질문은 못 보는 상황이 생겼다. (2) 대화 기록이 프로세스 메모리 버퍼(10줄·10분·300자)에만
있었으므로 재시작 시 소실됐다. (3) 에이전트가 직접 과거 메시지를 조회할 수단이
없었다.

## Task

- `decide_policy` 규칙 5를 수정해 다른 에이전트에게 보낸 메시지도 문맥으로 수신하되 응답은 억제할 것 (context-window opt-out 시 기존 SKIP 유지).
- 서버가 배달 시 `conversation_context`를 첨부해, 에이전트의 마지막 완료 턴 이후 같은 대화에서 발생한 메시지를 전달할 것. 첫 턴이면 최근 꼬리(최대 30개)를 제공.
- 에이전트가 직접 히스토리를 조회할 수 있는 `read_room_messages` MCP 도구를 추가할 것 (룸 참여자 전용, 메인 채널 또는 스레드 단위 페이지 조회).
- 새 도구를 Pi 브릿지 허용 목록에 등록할 것.

## Action

- `packages/cluster/anygarden/agent/integrations/base.py:899` — `decide_policy` 규칙 5에서 `SKIP` 대신 `INGEST_ONLY` 반환; `_context_window_opt_out` 플래그가 참이면 기존 SKIP 유지.
- `packages/cluster/anygarden/agent/integrations/base.py:334` — `EngineAdapter._build_message_list`에서 배달 메타데이터의 `conversation_context`를 `format_server_context`로 변환해 로컬 버퍼를 대체.
- `packages/cluster/anygarden/agent/coordination/pending_context.py:133` — `format_server_context` 함수 추가: 서버 context dict를 `[참고] speaker: content` 줄 목록으로 렌더링하고 생략 메시지 접두를 붙임.
- `packages/cluster/anygarden/turns/conversation_context.py` (신규) — `build_conversation_context`: 에이전트의 직전 완료 턴 트리거 이후 메시지를 조회하고 최대 30개를 `{version, messages, omitted}` dict로 반환.
- `packages/cluster/anygarden/messages/agent_view.py` (신규) — `in_conversation`, `describe`, `render_lines`: MCP 도구와 서버 context 양쪽에서 공유하는 메시지 렌더링 레이어. 숨겨진 시스템 메시지 필터링, 컨텐츠 1000자 잘라냄, 스레드 댓글 수 포함.
- `packages/cluster/anygarden/mcp/tools.py:267` — `read_room_messages` 스키마 및 핸들러 추가. `around_message_id`·`before_seq`·`limit` 매개변수 지원. `mcp/router.py`의 `tools/call`에 읽기 전용 분기 연결(커밋 전 검토에서 누락 발견 — 핸들러 직접 호출 테스트만으로는 RPC 미연결을 잡지 못해 `/mcp/rpc` 경유 테스트를 추가).
- `packages/cluster/anygarden/agent/runtime/execution/pi_self_tools.json` — `read_room_messages` 허용 목록 추가.
- `packages/cluster/anygarden/turns/service.py` — 배달 레이어에서 `build_conversation_context` 호출해 메타데이터에 첨부.
- `packages/cluster/anygarden/ws/handler.py` — WebSocket 핸들러에서 `conversation_context` 전달 경로 연결.
- 테스트: `tests/test_conversation_context.py` (181줄), `tests/test_mcp_read_room_messages.py` (177줄), `tests/test_durable_turns.py` (26줄), `tests/agent/test_integrations/test_base_adapter.py` (34줄), 외 다수. cluster 전체 `pytest -n logical` 3,860 passed(유일한 실패였던 도구 목록 고정 테스트 `test_mcp_server_create_skill.py`는 기대값에 `read_room_messages`를 추가해 해결).

## Decisions

- **INGEST_ONLY vs SKIP (규칙 5)**: 다른 에이전트에게 보낸 메시지를 완전히 버리면 3-에이전트 대화에서 문맥 불연속이 필연적으로 발생한다. INGEST_ONLY는 응답 억제(기존 보장)를 그대로 유지하면서 문맥만 흡수한다. context-window opt-out 에이전트는 추가 노이즈를 원하지 않으므로 SKIP을 유지.
- **서버 push vs 클라이언트 pull**: 로컬 버퍼(push)는 재시작 시 소실되고 300자 상한이 있었다. 서버 push(conversation_context)는 DB를 직접 읽으므로 재시작에 무관하고 더 정확하며, 클라이언트 pull(read_room_messages)은 에이전트가 필요할 때 추가 히스토리를 가져올 수 있는 보조 수단이다. 두 방식을 병행해 서버 context가 있으면 로컬 버퍼를 대체한다.
- **agent_view 공유 모듈**: MCP 도구와 서버 context 렌더링이 별도로 구현되면 표시 형식이 달라질 위험이 있다. 단일 `agent_view` 모듈로 통일해 일관성을 확보했다.
- **MAX_CONTEXT_MESSAGES = 30**: 너무 많으면 토큰 비용이 증가하고, 너무 적으면 문맥이 부족하다. 30개는 일반적인 대화 세션을 커버하기에 충분한 값으로 결정했다. 더 긴 기록이 필요하면 `read_room_messages`로 페이지 조회.

## Result

- 에이전트가 자신에게 보내지 않은 메시지도 문맥으로 수신해 대화 흐름을 파악할 수 있게 됐다.
- 서버가 배달마다 누락 대화를 첨부하므로 재시작 후에도 context 손실이 없다.
- `read_room_messages` MCP 도구로 에이전트가 직접 과거 메시지를 조회할 수 있다.
- 8개 테스트 모두 통과 (1.19s). 로스터가 에이전트에게 새 도구 사용을 안내한다.
- Pi 브릿지 허용 목록에 `read_room_messages` 등록 완료.
