# fix(agent): codex-cli 엔진 spawn 시 turn-timeout ValueError로 crash 수정 (#500)

- Commit: `69a4223`
- Author: Changyong Um
- Date: 2026-06-24
- PR: #500

## Situation

#496에서 `codex-cli`(codex exec) 엔진을 추가하고 #498에서 resume 버그를 고쳤지만, codex-cli 에이전트는 **spawn 자체가 안 됐다**. UI에서 codex-cli 에이전트를 만들어 메시지를 보내도 `message_received`만 기록되고 `handler_started`가 없었다(에이전트 부재). 머신 로그에 `agent_crashed exit_code=1`이 반복됐다.

## Task

- codex-cli 에이전트가 spawn 단계에서 죽지 않고 정상 기동하게 한다.
- 같은 부류(새 엔진 추가 시 spawn 경로 매핑 누락)의 재발을 막는다.

## Action

머신과 동일하게 에이전트를 수동 실행해 전체 traceback을 확보한 결과 `_turn_timeout.py:67 ValueError: unknown engine for turn timeout: 'codex-cli'`였다. `cli.py::_run_agent`가 spawn 시 `resolve_turn_timeout(engine_key)`로 WS ping_timeout을 계산하는데, `engine_key` 매핑(claude-code→claude, gemini-cli→gemini 등)에 codex-cli가 없어 `.get(engine, engine)`이 `"codex-cli"`를 그대로 넘겼고, `_ENGINE_DEFAULTS`(codex/claude/gemini/openhands)에 없는 키라 ValueError로 crash했다.

- `packages/agent/anygarden_agent/cli.py`: 인라인 `engine_key` dict를 모듈 상수 `_ENGINE_TIMEOUT_KEY`로 추출하고 `"codex-cli": "codex"` 추가(codex_cli 어댑터도 이미 `resolve_turn_timeout("codex")`를 호출하므로 동일 프로파일).
- `packages/agent/tests/test_integrations/test_codex_cli.py`: `TestEngineTimeoutKeyMapping` — codex-cli→codex 매핑 + 모든 ENGINES가 spawn timeout을 raise 없이 resolve하는지 회귀 가드.

(이 버그가 드러나기까지 PATH 문제도 함께 있었다: 머신을 `exec .venv/bin/anygarden`으로 띄우면 PATH에 `.venv/bin`이 없어 spawner의 `shutil.which("anygarden-agent")`가 실패→uvx로 PyPI 구버전을 받아 `--engine codex-cli`를 거부했다. 머신을 `uv run`으로 기동해 로컬 바이너리를 쓰게 하니 turn-timeout crash가 드러났다 — 이건 환경 기동 방식 문제라 코드 수정 대상은 아님.)

## Decisions

- **매핑 위치: 인라인 dict 유지 vs 모듈 상수 추출** → 모듈 상수 `_ENGINE_TIMEOUT_KEY`. 인라인이면 회귀 테스트가 spawn(async+WS)을 거쳐야 해 검증이 무겁다. 모듈 상수로 빼면 "모든 ENGINES가 resolve 가능"을 순수 단위 테스트로 가드할 수 있어, 다음에 엔진을 추가하다 매핑을 빠뜨리면 즉시 fail한다 — 이번 버그의 정확한 재발 방지점.
- **codex-cli의 timeout 키: 새 `_ENGINE_DEFAULTS["codex-cli"]` 추가 vs codex로 매핑** → codex로 매핑. 키 컨벤션이 짧은 이름(codex/claude/gemini)이고 codex-cli는 codex와 동일 바이너리/턴 특성이라 별도 default를 둘 이유가 없다. codex_cli 어댑터 자체도 `resolve_turn_timeout("codex")`를 쓰므로 일관된다.
- **발견 경로의 교훈**: 어댑터 단위 테스트(20개)는 통과했지만 cli.py spawn 경로를 거치지 않아 이 crash를 못 잡았다. 실전 E2E(UI 생성→spawn→대화)가 유일하게 잡았다 — spawn 경로를 단위로 가드하는 위 테스트를 추가한 이유.

## Result

codex-cli 에이전트가 crash 없이 spawn(PID 815739)되고, UI 멀티턴 대화에서 turn1 "안녕하세요, 박지원님!", turn2 "박지원"(이전 이름 기억=resume)으로 정상 동작 확인. 단위 22 passed, agent 전체 511 passed, ruff 통과. 이로써 codex-cli 엔진이 실전(생성·spawn·멀티턴·resume)에서 검증됐다.
