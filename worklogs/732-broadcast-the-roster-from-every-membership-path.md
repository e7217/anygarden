# fix(rooms): broadcast the roster from every membership path (#732)

- Commit: `9fd06e1` (9fd06e14cbad5da16df0708e2f8f9b744f68af35)
- Author: Changyong Um
- Date: 2026-09-28T19:37:23+09:00
- PR: #732

## Situation

#644의 `broadcast_roster()`는 `rooms/router.py`의 `add_participant`와 `remove_participant`에서만 호출됐다. 2026-09-28 룸 `9eb1547e…`에서 PM 에이전트를 만들면서 이 룸을 지정했다. 그런데 이미 연결돼 있던 agent01과 local-agent는 `room_settings_changed`를 받지 못했다. 그래서 둘 다 PM이 없는 명단으로 답했다: agent01은 PM의 발언을 무시했고, local-agent는 agent01을 PM으로 착각했다.

## Task

- 참가자를 추가하거나 제거하는 모든 경로가 commit 뒤에 룸 전체로 roster 스냅샷을 보낸다.
- 새 경로가 생겨도 누락되지 않도록, 가능하면 공통 헬퍼 안에서 보낸다.
- `create_agent`의 원자적 생성과 멱등 재시도 계약은 유지한다.
- roster가 바뀌지 않는 멱등 호출에서는 프레임을 보내지 않는다(#644 규칙).

## Action

- `packages/cluster/anygarden/rooms/membership.py`: `ensure_agent_in_room`은 `created=True`일 때만, `add_user_to_room`은 항상 commit·알림 뒤에 `broadcast_roster`를 호출한다.
- `packages/cluster/anygarden/rooms/router.py`: `add_participant`의 중복 `broadcast_roster` 호출을 제거했다(두 분기 모두 헬퍼를 거침).
- `packages/cluster/anygarden/api/v1/agents.py`: `create_agent`는 최종 commit 뒤에 `body.rooms`마다 best-effort로 broadcast한다(실패는 로그만 남김). `remove_agent_room`은 commit 뒤에 broadcast한다.
- `packages/cluster/anygarden/auth/routes.py`: guest 초대로 참가한 뒤 broadcast한다.
- `packages/cluster/anygarden/rooms/roster.py`: 호출 지점에 대한 docstring을 실제와 맞게 고쳤다.
- 테스트
  - `tests/test_room_roster_broadcast.py`에 `TestEveryMembershipPathBroadcasts` 5개를 추가했다: 에이전트 생성, 룸 추가·제거, 헬퍼 멱등 시 무음, guest 참가.
  - `tests/test_membership.py`: Fake 매니저에 `broadcast`를 기록하게 하고, 관련 단언을 추가했다.
  - `tests/test_ws_handler.py`: `#room` 자동 참가 때문에 소스 룸에 roster 프레임이 먼저 올 수 있어 `_receive_skipping_roster` 헬퍼를 넣었다. 소스 룸 roster 갱신을 단언하는 테스트도 추가했다.

## Decisions

- 브로드캐스트 위치
  - A. 공통 헬퍼 내부 — **채택**. `ensure_agent_in_room`을 쓰는 5개 경로가 한 번에 해결된다. #50/#227에서 JoinRoomOut 누락을 헬퍼 통합으로 해결한 선례와도 같다.
  - B. 각 라우터에서 호출 — 기각. 이번 버그가 바로 이 방식에서 생긴 누락이다.
  - C. SQLAlchemy `after_commit` 이벤트 — 기각. async 세션에서 `manager`를 엮어 WS를 보내는 구조가 과하다.
- 멱등 호출은 무음: `ws/handler.py`의 `#room` 자동 참가는 질의할 때마다 헬퍼를 부른다. roster가 그대로인데 매번 프레임을 보내면 #644 규칙에 어긋난다.
- `create_agent`를 헬퍼로 교체하지 않음(이슈 제안에서 벗어남): 헬퍼는 중간에 commit하고 `representative_agent_id`를 자동으로 채운다(#312). 둘 다 에이전트·DM 룸 원자적 생성과 `creation_request_key` 멱등 계약을 깬다. 그래서 직접 삽입은 유지하고, commit 뒤에 broadcast만 추가했다.
- 제거 로직 통합은 범위에서 뺐다: `remove_participant`는 태스크 해제·대표 재선정·파일·워크스페이스 정리까지 해서 `remove_agent_room`과 책임이 다르다.
- 가정: 새 룸에 참가하는 에이전트는 `join_room` → 새 WS → `welcome`으로 최신 명단을 받는다. 그래서 sub-room 생성이나 새 DM처럼 새 룸이 생기는 경로는 기존 관찰자가 없어 영향이 없다.

## Result

- 에이전트 생성(rooms 지정), `POST/DELETE /agents/{id}/rooms`, `#room` 대표 자동 참가, guest 초대 참가 모두 기존 참가자에게 `room_settings_changed.participants`를 보낸다.
- 동작 변화: `#room` 멘션으로 대표가 소스 룸에 새로 참가하면, 소스 룸 구독자가 `message` 앞에 roster 프레임을 받는다.
- `packages/cluster` 전체 테스트 1981 passed. 변경 파일의 ruff 오류 수는 main과 같다.
- 남은 일: 라이브 노드에서 룸을 지정해 에이전트를 만든 뒤 기존 에이전트 로그의 `room_settings_changed`를 수동으로 확인.
