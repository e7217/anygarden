# test(ci): fix ws_env teardown flake at the real site — defensive session close (#468)

- Commit: `3da65cd`
- Author: Changyong Um
- Date: 2026-06-19
- PR: #468

## Situation

`test_ws_handler.py::TestWSEndpoint::test_ws_rejects_cross_room_shared_file_reference`가 CI에서 간헐 **teardown ERROR**(`no active connection`)로 실패. #464/#466에서 고치려 했으나 **#466이 `engine.dispose()`(line 90)를 감쌌고, 실제 실패 지점은 그 앞 `async with session_factory() as db:` 세션 컨텍스트 종료(`__aexit__`) 시 rollback**이었다. 릴리즈 PR #467 CI에서 동일하게 재발(재실행으로 통과).

## Task

엉뚱한 줄을 감싼 #466을 바로잡아 실제 실패 지점을 방어. 단, ws_env는 in-memory(`sqlite+aiosqlite://`) 공유 커넥션으로 데이터 가시성을 유지하려 `yield`를 세션 블록 안에 두므로, 세션을 닫아 yield 밖으로 빼면 데이터가 사라질 위험 → 세션은 열어둔 채 종료만 방어적으로.

## Action

`packages/cluster/tests/test_ws_handler.py` ws_env fixture:
- `async with session_factory() as db:` → 수동 `db = session_factory()` + `try:` 본문 + `finally:`.
- `finally`에서 `await db.close()`와 `await engine.dispose()` 모두 `try/except Exception`으로 감싸 종료 race 흡수.
- 본문(시드/커밋/refresh/create_app/yield)은 그대로 — 세션이 yield 동안 열려 있어 in-memory 데이터가 app 세션에 계속 보임.

## Decisions

- **세션을 yield 전에 닫지 않음** — in-memory 단일 공유 커넥션을 놓으면(pool이 새 빈 커넥션을 줄 수 있어) 시드 데이터가 app에 안 보일 위험. 그래서 "열어둔 채 종료만 방어"가 정답.
- **`__aexit__`를 try로 감쌀 수 없어 수동 open으로 전환** — `async with`의 종료는 inline try/except 불가. 수동 세션 + finally가 유일하게 종료 rollback을 흡수하는 방법.
- **WS 테스트 한정 근본 원인** — 핸들러 태스크 취소(TestClient websocket 종료)가 공유 커넥션을 닫는 게 트리거. 비-WS REST fixture는 깨끗이 닫혀 영향 없음 → ws_env만 수정(최소 변경).
- `except Exception` 광범위 흡수 — throwaway in-memory DB의 teardown이라 안전, 레포 ruff에 BLE001/S110 미활성이라 신규 lint 0.

## Result

- 해당 테스트 **10/10 passed**, `test_ws_handler.py` 45 passed ×5(teardown 에러 없음), cluster **1175 passed**. 신규 ruff 에러 0.
- #466의 미완성 수정을 정정해 CI flake의 실제 원인을 제거. 향후 릴리즈/PR CI가 재실행 없이 안정.
