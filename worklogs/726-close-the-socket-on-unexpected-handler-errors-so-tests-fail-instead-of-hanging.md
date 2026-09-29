# fix(ws): close the socket on unexpected handler errors so tests fail instead of hanging (#726)

- Commit: `745dbdf` (745dbdf4619446f30ec20875a84ca0c80da97719)
- Author: Changyong Um
- Date: 2026-09-29T17:42:52+09:00
- PR: #726 (issue)

## Situation

Linux CI의 `Test cluster` 단계가 `tests/test_ws_handler.py`에서 간헐적으로 멈췄다. 정상 실행은 약 12분인데, 멈추면 GitHub 기본 제한인 6시간이 지나서야 취소됐다. main(2026-09-28, `TestAgentCausalLink` 구간)과 PR #738(두 번 연속)에서 관찰됐고, 로컬 전체 스위트에서도 한 번 재현됐다. 로컬 재현에서 faulthandler 스택을 떠 보니 `TestAgentCausalLink::test_directed_delegation_creates_only_target_child_turn[member-round_robin]`이 `_agent_send`의 `ws.receive_text()`에서 대기하고 있었다. 이때 서버 쪽 portal 루프와 aiosqlite 워커 스레드는 모두 idle이었다. #726은 같은 파일에서 SQLite "no active connection" 오류로 간헐적으로 실패하는 문제를 이미 기록하고 있었다.

## Task

- 테스트 멈춤을 실패로 바꿔서 CI가 원인을 드러내게 한다.
- 멈춤이 생겨도 CI 러너를 6시간 붙잡지 않게 한다.
- 다음 멈춤에서 어떤 테스트가 어디서 멈췄는지 로그에 남게 한다.

## Action

- `packages/cluster/anygarden/ws/handler.py`: 프레임 루프의 `except Exception` 경로에서 `ws.error`를 로깅한 뒤 `websocket.close(code=1011, reason="internal error")`를 best-effort로 호출한다.
- `packages/cluster/tests/test_ws_handler.py`: `TestWSEndpoint::test_ws_unexpected_error_closes_socket`를 추가했다. `app.state.typing_tracker.set_typing`이 예외를 던지게 만들고 typing 프레임을 보낸 뒤, 클라이언트가 `WebSocketDisconnect(1011)`를 받는지 확인한다. 수정 전 코드에서는 이 테스트가 무기한 대기해 `timeout 60`으로 강제 종료됐다.
- `.github/workflows/ci.yml`: `test-linux` 잡에 `timeout-minutes: 45`를 추가했다.
- `packages/cluster/pyproject.toml`: `faulthandler_timeout = 300`을 추가했다.

## Decisions

- **멈춤의 메커니즘**: starlette `WebSocketTestSession._run`은 앱이 close 없이 반환하면 `anyio.sleep_forever()`에 들어간다. 따라서 핸들러가 예외를 삼키고 반환하면 테스트 클라이언트의 `receive_text()`는 영원히 대기한다. 운영 환경에서는 uvicorn이 연결을 정리하지만, 클라이언트가 받는 종료 코드는 의미가 모호하다. 1011은 표준 "internal error"이고, 프론트 `useWebSocket`은 이 코드를 일반 재접속 대상으로 처리한다.
- **고려한 대안**
  - (A) 핸들러에서 close(1011): 운영 의미도 개선되고, 확정 재현 테스트로 고정할 수 있다. → 채택
  - (B) 테스트 헬퍼마다 receive 타임아웃 추가: TestClient의 `receive`에는 타임아웃 인자가 없어 스레드나 portal로 우회해야 하고, 모든 호출부를 바꿔야 한다. → 기각
  - (C) `pytest-timeout` 도입: 의존성이 늘고, 멈춤을 실패로 바꿀 뿐 원인을 드러내지 못한다. 표준 기능인 `faulthandler_timeout` + CI `timeout-minutes`로 충분하다. → 기각
- **근본 원인은 미확정**: CI 조건을 흉내 낸 로컬 진단 실행(2코어 고정, 전체 스위트, asyncio 태스크 덤프, `ws.error` 기록)에서는 멈춤이 재현되지 않았다(2022 passed). 그래서 핸들러 안에서 어떤 예외가 났는지는 확인하지 못했다. #726의 SQLite 연결 오류가 유력한 후보다. 인메모리 aiosqlite(StaticPool 단일 연결, SQLAlchemy `_execute_mutex`)를 async 테스트 루프와 TestClient portal 루프가 공유하는 구조가 의심되지만 검증하지 않았다.
- **재검토 신호**
  - 멈춤이 예외 경로가 아니라 "영원히 resolve되지 않는 await"라면 이번 수정으로는 해결되지 않는다. 그 경우 `faulthandler_timeout` 덤프와 CI 45분 캡이 흔적을 남긴다.

## Result

- 핸들러 예외 시 테스트가 멈추지 않고 1011 close로 실패한다(재현 테스트 추가, 수정 전 멈춤 확인).
- CI Linux 잡은 최대 45분으로 제한되고, 5분 넘게 도는 테스트는 스택이 덤프된다.
- WS 관련 테스트 161개 통과.
- 남은 일: 핸들러 예외를 일으키는 근본 원인(#726의 SQLite 공유 연결 문제)은 그대로 남아 있다. #726은 열어 둔다.
