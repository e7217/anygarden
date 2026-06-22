# fix(agent): claude-code/openhands 어댑터 자체 타임아웃 대칭화 (#483)

- Commit: `832c2eb` (832c2ebb8136ba7b1754d263392686850d59523d)
- Author: Changyong Um
- Date: 2026-06-22T23:48:14+09:00
- PR: #483

## Situation

엔진 어댑터의 타임아웃 방어가 비대칭이었다. codex(`codex.py:465`)는 자체 600s + `EngineTimeoutError` + `abort_signal` 취소, gemini-cli(`gemini_cli.py:408`)는 자체 120s + `EngineTimeoutError` + `_terminate_tree` 취소를 갖는 반면, claude-code / openhands는 자체 타임아웃이 전무해 supervisor의 `wait_for`(기본 900s)가 유일한 방어선이었다. 결과적으로 claude-code/openhands가 매달리면 룸이 최대 900s 점유돼 codex/gemini 대비 4~7.5배 늦게 사용자에게 통지됐다. 특히 openhands는 `asyncio.to_thread(conversation.run)`(`openhands_engine.py:324`) 구조라 supervisor가 awaiting 코루틴을 취소해도 워커 스레드(와 in-flight LLM 호출)가 좀비로 남았다.

## Task

- codex/gemini가 이미 쓰는 `asyncio.wait_for(coro, timeout=T)` → `except asyncio.TimeoutError: raise EngineTimeoutError(...)` 패턴을 claude-code/openhands에 대칭 적용한다.
- 어댑터 자체 타임아웃은 supervisor(900s)보다 작게(=어댑터가 먼저 발화) 두고, openhands는 가능한 경우 SDK 취소 API로 좀비를 완화한다.
- 정상 응답 경로는 불변. `runtime/handler_wrapper.py` / `client.py` / `integrations/base.py`는 수정하지 않는다(#482 소관). `EngineTimeoutError`는 import해서 사용만 한다.

## Action

- `packages/agent/anygarden_agent/integrations/claude_code.py`: `_CLAUDE_TURN_TIMEOUT`(기본 600s, env `ANYGARDEN_AGENT_CLAUDE_TURN_TIMEOUT_SEC` override) 상수를 도입. `_collect_reply`의 `query()` async-generator 소비를 inner 코루틴 `_consume()`로 분리하고 `asyncio.wait_for(_consume(), timeout=_CLAUDE_TURN_TIMEOUT)`로 감쌌다. generator 핸들을 명시적으로 잡아 `finally`에서 `aclose()`로 SDK 스트림(transport/subprocess)을 정리해 timeout 시 누수를 막고, `asyncio.TimeoutError`를 `EngineTimeoutError`로 표면화한다.
- `packages/agent/anygarden_agent/integrations/openhands_engine.py`: `_OPENHANDS_TURN_TIMEOUT`(기본 600s, env `ANYGARDEN_AGENT_OPENHANDS_TURN_TIMEOUT_SEC` override) 상수를 도입. `on_message`의 `to_thread(conversation.run)` await를 `wait_for`로 감싸 timeout을 `EngineTimeoutError`로 표면화. timeout 시 신설 헬퍼 `_request_conversation_pause`가 `conversation.pause()`(OpenHands SDK v1.29 — 어느 스레드에서나 호출 가능, agent-step 경계에서 효력)를 best-effort로 호출해 좀비 스레드를 완화한다. `pause` 미지원(구버전) SDK는 `getattr` 가드 + 광범위 `except`로 보호해 cancel 부재가 `EngineTimeoutError` 표면화를 가리지 못하게 했다. 기존 1007 근방의 `engine_timeout`은 supervisor 전용(900s)으로 어댑터 자체 타임아웃과 별개임을 확인하고 충돌을 피하기 위해 어댑터는 전용 env 키를 사용한다.
- 테스트(TDD): `test_claude_code.py::TestClaudeTurnTimeout`(hang하는 fake `query()` → `EngineTimeoutError` + 스트림 cleanup, happy-path 불변), `test_openhands_engine.py::TestOpenHandsTurnTimeout`(hang `run` → `EngineTimeoutError` + `pause()` 호출 검증, pause 미지원 SDK에서도 timeout 표면화, happy-path 불변).

## Result

agent 패키지 전체 `uv run pytest packages/agent -q` → 460 passed (claude_code 39, openhands 62 포함, 신규 5건). 변경 4개 파일 `uv run ruff check` → All checks passed (레포 전체 ruff의 8개 에러는 모두 본 작업이 건드리지 않은 pre-existing 파일 — `test_room_query.py`, `client.py`, `test_cli.py` 등). claude-code/openhands가 이제 supervisor(900s)보다 먼저(기본 600s) 자체 타임아웃을 발화해 codex/gemini와 통지 대칭성을 확보했고, openhands 좀비는 `pause()` best-effort로 완화(in-flight LLM 완료까지는 못 죽이는 SDK 한계는 잔존, 후속). 변경: +324 / −3, 4파일(어댑터 2 + 테스트 2).
