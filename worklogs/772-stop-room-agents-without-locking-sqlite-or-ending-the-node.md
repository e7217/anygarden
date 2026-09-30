# fix(rooms): stop room agents without locking SQLite or ending the node (#772)

- Commit: `d97a1b2` (d97a1b2bc539d96ef2a3513ffe59606cf03c1f7e)
- Author: Changyong Um
- Date: 2026-10-01T02:45:31+09:00
- PR: #772

## Situation

2026-10-01 전체 페이지 점검(247 배포, v0.19.0 통합 노드)에서 룸 메뉴의 "모든 에이전트 중지"를 누르자 `POST /api/v1/rooms/{id}/stop-agents`가 500을 반환했다. 직후 노드 전체가 약 30~40초 내려갔다가 슈퍼바이저에 의해 재시작됐다. 에이전트 2개 중 첫 번째만 중지됐고, 이 메뉴는 확인 없이 즉시 실행되며 실패도 화면에 표시되지 않았다.

## Task

- 룸 단위 중지가 SQLite 락 없이 모든 대상 에이전트를 중지하게 한다
- 통합 노드가 일시적인 DB 오류 하나로 전체 종료되지 않게 한다. 소유권 불변식 위반 시 종료하는 동작은 유지한다
- 파괴적인 메뉴에 확인 다이얼로그와 결과 표시를 추가한다
- 제약: `AgentLifecycle.request_stop`의 트랜잭션 계약(generation 펜스 → commit → 프레임 전송)은 건드리지 않는다

## Action

- `packages/cluster/anygarden/rooms/router.py` `stop_all_agents_in_room`
  - `Agent ⨝ Participant` 단일 SELECT로 실행 중인 대상 ID를 수집한다
  - `await db.commit()`으로 요청 트랜잭션을 닫은 뒤, 에이전트마다 `request_stop`만 호출한다
  - 중복이던 `agent.desired_state = "stopped"` 대입을 제거했다
  - 에이전트별 예외를 모아 `{stopped, failed, count}`로 반환하고, 실패는 structlog 경고로 남긴다
- `packages/cluster/anygarden/api/v1/agents.py` `stop_agent`: `request_stop` 뒤의 중복 쓰기와 commit을 제거하고 `refresh`만 한다
- `packages/cluster/anygarden/node/execution.py`
  - `_handle_frame`을 추가했다. 프레임 처리 예외는 `log.exception` 후 계속하고, `NodeOwnershipError`는 다시 던진다
  - `_maintain`은 틱 단위로 예외를 잡는다. `MAX_MAINTENANCE_FAILURES`(40)번 연속 실패할 때만 종료 경로로 넘어간다
- `packages/cluster/frontend/src/pages/ChatPage.tsx`: `handleStopAllAgents`를 추가했다. `confirm`(destructive) 후 호출하고, 성공·부분 실패·실패를 `notify`로 알린다. i18n ko/en 키 5개는 `i18n/catalogs/chat.ts`에 추가했다
- 테스트
  - `tests/test_room_stop_agents.py` 신규(파일 DB): 3개 동시 중지, 부분 실패, 대상 없음
  - `tests/test_local_node.py`: 일시적 프레임 오류로 종료되지 않음, 소유권 오류는 종료, 유지보수 일시 오류 허용, 연속 실패 시 종료
  - `tests/test_agents_api.py`: 단건 stop 테스트를 메모리/파일 DB 양쪽으로 매개변수화

## Decisions

- **락 역전을 끊는 위치**
  - 채택: 엔드포인트에서 세션을 정리하는 방식. 요청 세션의 쓰기는 `request_stop`이 원자적 UPDATE로 이미 기록하는 내용의 중복이라, 없애도 잃는 동작이 없다
  - 기각: `request_stop`에 요청 세션을 넘기는 방식. 커밋 시점이 호출자에게 넘어가 "커밋 전 프레임 전송" 순서가 깨질 위험이 있다
  - 기각: WAL과 `busy_timeout`만 추가하는 방식. 쓰기 락을 쥔 채 다른 쓰기를 기다리는 역전은 timeout을 늘려도 더 오래 막힐 뿐 해소되지 않는다. WAL/`busy_timeout` 자체는 전역 영향이 있어 별도 과제로 남겼다
- **통합 노드의 오류 경계**
  - 원격 데몬(`machine/daemon.py`의 WS 루프)은 프레임 처리 예외를 로그로 남기고 계속 진행한다. 통합 모드만 노드 전체를 끄는 것은 일관성이 없어 원격과 같은 수준으로 맞췄다
  - 모든 예외를 무시하는 안은 소유권 상실처럼 진짜 치명적인 상태까지 삼키므로 기각했다
- **부분 실패 응답 형태**
  - 항상 200에 `failed` 목록을 담는다. 500으로 응답하면 이미 중지된 에이전트가 있다는 사실이 사라진다(이번 사고에서 garden-pm만 중지된 상태가 UI에 보이지 않았다). 기존 `stopped`, `count` 필드는 유지했다
- **재검토 신호**
  - 프레임을 잃어도 주기 보고와 lifecycle 재조정으로 상태가 수렴한다는 가정에 기댄다. 상태가 영구히 어긋나는 사례가 나오면 다시 검토해야 한다
  - `MAX_MAINTENANCE_FAILURES` 값 40은 경험적으로 정한 값이다

## Result

- 파일 DB 재현 테스트가 수정 전에는 `sqlite3.OperationalError: database is locked`로 실패했고(원인 추정 확정), 수정 후 통과한다
- 통합 노드는 프레임 하나나 유지보수 틱의 일시 오류로 종료되지 않는다
- 사용자는 확인을 거친 뒤 중지하고, 결과(부분 실패 포함)를 알림으로 받는다
- 검증: cluster 전체 2682 passed / 2 skipped, 프론트 vitest 791 passed, `npm run build` 성공
- 남은 것
  - 로컬 통합 노드에서의 수동 E2E 확인과 247 재배포 후 재확인
  - SQLite WAL/`busy_timeout` 도입 여부는 별도 과제
