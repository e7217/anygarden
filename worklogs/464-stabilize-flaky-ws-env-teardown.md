# test(ci): stabilize flaky ws_env teardown (aiosqlite no active connection) (#464)

- Commit: `a86b55e` (a86b55efd0ee0e8a433963ce6514cd11b9918207)
- Author: Changyong Um
- Date: 2026-06-19T08:26:54+09:00
- PR: #464

## Situation

Wave 2b PR #458 CI(Test Linux)에서 `test_ws_handler.py::TestWSEndpoint.test_ws_rejects_cross_room_shared_file_reference`가 **teardown ERROR**(aiosqlite `no active connection` — SQLAlchemy `do_rollback`)로 간헐 실패했다. 재실행 시 통과했고 로컬 `uv run pytest -x` 재현도 안 됐다 — 단언 실패가 아니라 `ws_env` fixture의 정리 단계 race였다. TestClient websocket 컨텍스트가 종료될 때 WS-handler 태스크가 취소되며 in-memory aiosqlite 커넥션이 닫힌 채 풀에 남고, `engine.dispose()`가 그 커넥션을 rollback하려다 "no active connection"을 던진다.

## Task

버려질 in-memory DB의 정리 race가 통과한 테스트를 teardown ERROR로 만들지 않게 fixture teardown을 방어적으로. 코드 동작/단언은 불변(테스트 인프라만).

## Action

2 파일 +16/-2.

- `packages/cluster/tests/test_ws_handler.py` — `ws_env` fixture의 `await engine.dispose()`(line 90, 4-space)를 try/except(Exception)로 감싸 정리 race 흡수.
- `packages/cluster/tests/conftest.py` — 공용 `engine` fixture의 `await eng.dispose()`도 동일 방어(WS-capable 테스트가 광범위 사용하는 fixture라 동일 race 가능).

## Decisions

- **방어적 dispose(try/except) — 근본 원인 수정 대신** — 근본은 TestClient WS + async sqlite의 핸들러-태스크-취소 시 커넥션 lifecycle인데, 핸들러의 커넥션 생명주기를 테스트 위해 바꾸는 건 과함. in-memory throwaway DB의 dispose는 커넥션 닫기뿐이라 거기서의 오류는 cleanup race일 뿐 테스트 정확성과 무관 → 흡수가 올바른 스코프.
- **ws_env + conftest engine만, 다른 8-space dispose 사이트는 보존** — 관측된 flake는 ws_env. `replace_all`이 8-space 라인의 4-space 부분문자열을 오매칭해 들여쓰기를 깨뜨린 1차 시도를 되돌리고, `}`+blank 컨텍스트로 ws_env(4-space)만 정밀 매칭. 다른 fixture는 미관측이라 최소변경.
- **except Exception(좁히지 않음)** — 테스트 teardown의 throwaway DB이므로 광범위 흡수가 안전하고, 이 레포 ruff config는 BLE001/S110 미활성이라 신규 lint 에러도 없음.

## Result

- `test_ws_handler.py` 45 passed ×3회(teardown 에러 없음), 전체 cluster **1175 passed**. 신규 ruff 에러 0(기존 import 부채만).
- 효과: 해당 WS teardown race가 더 이상 CI를 ERROR로 만들지 않음(방어적 흡수). 동작 무변경.
- 참고: 이 flake는 #458 CI에서 1회 관측 후 재실행 통과했던 기존 문제. 근본적 WS-fixture 커넥션 lifecycle 개선은 별도 여지.
