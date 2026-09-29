# fix(ws): let a user keep the same room open in several tabs (#731)

- Commit: `c015533` (c01553347d26e495e5e79272e7317b3299a0ea2a)
- Author: Changyong Um
- Date: 2026-09-29T11:07:42+09:00
- PR: #731 (issue)

## Situation

같은 사용자가 같은 룸을 두 개 이상의 탭·창·기기에서 열면 모든 탭이 같은 룸 단위 `participant_id`로 WS에 접속했다. `ConnectionManager.subscribe()`는 #79 single-session 정책에 따라 participant당 소켓 하나만 유지하므로 새 연결이 올 때마다 기존 소켓을 `4040 superseded`로 닫았다. 클라이언트 `useWebSocket`은 4040도 1초 뒤 재접속했고, `onopen`에서 backoff가 초기화되어 탭끼리 서로를 끊는 루프가 끝없이 돌았다. 그 결과 상태 배지가 1~3초 간격으로 연결됨/끊김을 오가고 입력창이 잠겼다 풀리기를 반복했다.

## Task

- 사람 세션(user/guest)은 같은 participant로 여러 소켓을 유지할 수 있어야 한다.
- agent 연결에는 #79의 4040 supersede 동작을 그대로 유지한다 (토큰 공유로 브로드캐스트가 중복되면 LLM 호출이 두 배가 됨).
- presence는 탭 하나를 닫았다고 offline이 되면 안 된다.
- 클라이언트는 4040을 받아도 재접속 루프를 만들지 않는다.

## Action

- `packages/cluster/anygarden/ws/manager.py`
  - `_by_participant`를 `dict[str, list[_Subscription]]`로 바꾸고 인덱스 갱신을 `_attach_locked`/`_detach_locked`로 모았다. 제거는 소켓 identity 기준이다.
  - `subscribe(..., exclusive=True)`: exclusive면 기존 소켓을 모두 4040으로 닫고, 아니면 공존시킨다. online presence는 첫 소켓일 때(또는 exclusive일 때)만 발행한다.
  - `unsubscribe(pid, websocket=...)`: 해당 소켓만 제거한다. 마지막 소켓이 빠질 때만 `_last_seen`을 기록하고 offline을 발행한다. `websocket` 없이 호출하면 전부 제거한다.
  - `send_to`/`push_to_users`는 모든 소켓에 전송한다. `send_to`는 하나라도 성공하면 `True`를 반환하고, generation/epoch fence는 소켓별로 비교한다. `participant_generation`/`execution_connection`은 최신 소켓을 기준으로 한다. `revoke_room`은 소켓 단위로 닫고 해제한다. `active_connections`는 소켓 수를 센다.
- `packages/cluster/anygarden/ws/handler.py`: `manager.subscribe(..., exclusive=identity is None or identity.kind == "agent")`.
- `packages/cluster/frontend/src/hooks/useWebSocket.ts`: `onclose`에서 4040이면 `supersededRef`만 세우고 재접속 타이머를 걸지 않는다. `visibilitychange`(hidden 아님)/`focus` 시 superseded 상태면 `connect()`한다.
- 테스트
  - `tests/test_ws_manager_single_session.py`: 공존, 부분 해제 시 online 유지, 마지막 해제 시 offline, 모든 탭 전송, `send_to` 부분 실패, `revoke_room`, exclusive가 모든 소켓을 supersede하는 경우를 추가했다.
  - `tests/test_ws_handler.py`: 같은 user 두 탭이 공존하는 통합 테스트(수정 전 실패 확인)와 agent 두 연결에서 첫 연결이 4040을 받는 통합 테스트를 추가했다.
  - `useWebSocket.test.tsx`: 4040 무재접속, 가시성/포커스 복귀 시 재연결, 언마운트 후 리스너 해제를 추가했다.

## Decisions

- **수정 위치**
  - 고려한 대안: (A) 클라이언트만 고치기(4040이면 재접속 중단 + 안내 UI), (B) 서버만 고치기(사람 세션 다중 소켓), (C) 서버 근본 수정 + 클라이언트 방어.
  - C를 택했다. #79 docstring이 밝힌 목적은 agent 토큰 중복 연결로 생기는 부작용이다. 사람 탭은 수신만 하고, `push_to_users`도 원래 "두 탭이면 두 번 받는다"를 의도로 적어 두었다. 사람 세션을 single-session으로 묶을 이유가 처음부터 없었다.
  - A는 한 탭이 항상 끊긴 채로 남아 멀티 기기 사용성이 나쁘다. B만 하면 구버전 서버와 섞인 배포에서 루프가 남는다.
- **자료구조**: participant→소켓 리스트를 택했다. 공개 API 시그니처가 그대로이고 agent는 길이 1이라 fence 로직이 단순하다. 소켓 키 중심 재구성은 pid 기반 API 10여 곳의 의미를 재정의해야 해서, 탭별 가상 participant는 presence·권한·발신자 모델을 흔들어서 기각했다.
- **exclusive 판정**: `user_id` 유무가 아니라 identity kind로 판정한다. guest는 `user_id=None`이라 `user_id` 기준이면 guest 다중 탭 루프가 남는다. 기본값을 `True`로 둬서 기존 호출과 테스트 더블은 #79 동작을 유지한다.
- **클라이언트 UI**: 새 UI는 추가하지 않았다. 서버 수정 후에는 사람 세션이 4040을 받지 않으므로 이 분기는 안전장치일 뿐이고, 기존 "연결 끊김" 배지로 충분하다 (AGENTS.md의 선택지 노출 원칙).
- **typing 자기 표시**: 서버는 typing을 발신자 participant에게도 보내지만 `TypingIndicator`가 `myParticipantId`를 걸러내므로 서버를 바꾸지 않았다.
- **재검토 신호가 되는 가정**
  - 한 탭은 룸 WS를 하나만 연다. 탭 안에서 중복 연결이 생기면 이제 끊기지 않고 공존하므로 누수가 가려진다. `active_connections`가 실제 탭 수보다 크게 관측되면 재검토한다.
  - 사람 participant에는 generation/execution_control이 설정되지 않는다.
- 상세 비교는 `.tmp/plan-731-ws-multi-tab-superseded-loop.md`에 있다.

## Result

- 같은 룸을 두 탭에서 열어도 연결 상태가 번갈아 바뀌지 않는다. scratch 서버와 Playwright 두 탭으로 확인했다: 20초 동안 입력창이 한 번도 잠기지 않았고, WS 연결은 2회, 4040은 0회였으며, 탭 간 메시지 수신이 되고, 한 탭을 닫아도 다른 탭은 연결을 유지했다.
- agent 연결은 기존대로 4040으로 supersede된다(통합 테스트).
- cluster pytest 1985 passed, 프론트 vitest 758 passed, `npm run build` 성공.
- 참고: 최초 전체 pytest 실행이 다른 워크트리의 동시 pytest들과 겹친 상황에서 idle 상태로 9시간 넘게 멈췄다. 재실행(faulthandler_timeout=180)에서는 멈춤 없이 통과해 원인을 특정하지 못했다.
