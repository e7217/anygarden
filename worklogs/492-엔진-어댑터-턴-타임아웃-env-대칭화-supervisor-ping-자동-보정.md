# feat(agent): 엔진 어댑터 턴 타임아웃 env 대칭화 + supervisor/ping 자동 보정 (#492)

- Commit: `0e3f339` (0e3f3394e4b2c093253c97735eccde1fb5909e37)
- Author: Changyong Um
- Date: 2026-06-23T20:12:24+09:00
- PR: #492

## Situation

긴 에이전트 응답이 어댑터 턴 타임아웃에 걸려 잘리는데, 4개 엔진 어댑터의 타임아웃 설정 방식이 비대칭이었다. claude-code/openhands는 #483에서 전용 env(`ANYGARDEN_AGENT_CLAUDE/OPENHANDS_TURN_TIMEOUT_SEC`)로 조정 가능해졌지만, codex(600s)/gemini(120s)는 여전히 하드코딩 모듈 상수라 글로벌 조정조차 불가능했다. 또한 타임아웃을 키워도 WS `ping_timeout`(600s 하드코딩)이나 supervisor(900s)가 먼저 터지면 silent-drop/조기 종료가 발생해, 운영자가 한 레이어만 손대면 문제가 풀리지 않는 구조였다.

## Task

- codex/gemini 어댑터 턴 타임아웃을 env 오버라이드 가능하게 만들어 4엔진을 대칭화.
- 턴 타임아웃 N으로부터 `ping_timeout`과 supervisor `engine_timeout`을 자동 도출해 불변식 `turn < ping ≤ supervisor`를 코드로 보장.
- 기존 claude/openhands env 키는 보존(운영 env 회귀 금지).
- agent 패키지 단독 변경으로 머지 즉시 글로벌 env 조정이 가능해야 함.
- 후속 #493(per-agent)이 얹힐 헬퍼 구조를 마련.

## Action

- `integrations/_turn_timeout.py` 신규: `resolve_turn_timeout(engine)`(우선순위 `ANYGARDEN_AGENT_<ENGINE>_TURN_TIMEOUT_SEC` > 하드코딩 기본 `_ENGINE_DEFAULTS`), `resolve_supervisor_timeout(turn)`(`max(turn+300, env_floor, 900)`), `resolve_ping_timeout(turn)`(`max(turn+60, 600)`). slack 상수 `PING_SLACK=60`, `SUP_SLACK=300`.
- `integrations/codex.py:108`·`gemini_cli.py:140`: 하드코딩 상수 → `resolve_turn_timeout(...)`. supervisor 진입점(`codex.py:616`, `gemini_cli.py:586`)의 `os.environ.get(...)` → `resolve_supervisor_timeout(...)`.
- `integrations/claude_code.py:76`·`openhands_engine.py:74`: 기존 env 읽기 → 동일 헬퍼로 통일(env 키 `claude`/`openhands` 매핑이 기존 키와 일치). supervisor 진입점도 헬퍼 사용. 미사용이 된 `os` import는 ruff `--fix`로 정리.
- `client.py`: `ChatClient.__init__`에 `ping_timeout: float = 600.0` 인자 추가(`self._ping_timeout`), WS connect의 `ping_timeout=600` → `self._ping_timeout`.
- `cli.py` `_run_agent`: 엔진명→키 매핑(`claude-code→claude`, `gemini-cli→gemini`)으로 `resolve_ping_timeout(resolve_turn_timeout(...))` 계산해 `ChatClient(ping_timeout=...)`에 전달. 텍스트 클라이언트(`_run_client`)는 엔진이 없어 기본 600 유지.
- `tests/test_turn_timeout.py` 신규: 우선순위 체인, floor/slack, 불변식, `ChatClient` ping 배선(기본/오버라이드) 검증(17 케이스).

## Decisions

- **헬퍼를 신규 모듈로 분리** vs 어댑터 인라인 vs `base.py` 추가: 불변식 `turn < ping ≤ supervisor`를 한 곳에서 계산해야 코드로 강제·단위 테스트 가능. #483식 어댑터 인라인은 보정 공식이 4곳에 중복되어 한 곳만 어긋나도 silent-drop 회귀를 부른다. `base.py`는 엔진 추상 인터페이스 책임과 무관해 응집도 저하. → 신규 `_turn_timeout.py` 채택.
- **ping 배선을 cli.py 한 곳(DRY)** vs 어댑터별 4곳: 어댑터는 `_setup_engine`이 `client.run()` 전에 awaited되므로 어느 쪽이든 시점은 안전. CLI 엔진명(`claude-code`/`gemini-cli`)이 헬퍼 키(`claude`/`gemini`)와 달라 매핑이 필요하지만, 매핑을 `--engine` choices를 소유한 cli.py에 두면 4개 어댑터 편집 없이 한 곳에서 끝나 변경면이 작다. → cli.py 단일 배선 채택.
- **slack 값 PING_SLACK=60 / SUP_SLACK=300**: 현재 supervisor(900)−turn(600)=300 관계를 보존하고(orphan 주석 "engine timeout 15분 + 5분 slack"과 정합), ping은 현재 turn==ping==600 경계라 60s 마진으로 안전 확보. floor(ping 600 / supervisor 900)로 작은 N(gemini 120 등)은 기존 동작 유지.
- **per-agent leg는 이 PR에서 제외**: PR 분리 명확성을 위해 #492는 글로벌만. `resolve_turn_timeout` 체인 맨 앞 per-agent env(`ANYGARDEN_AGENT_TURN_TIMEOUT_SEC`)는 #493에서 한 줄 추가.
- **가정**: orphan 임계값 1200s 고정. 이를 키우면 #493의 N 상한 검증식이 따라가야 함. env는 import-time 1회 읽기라 값 변경은 에이전트 재시작 시 반영(기존 동작과 동일).

## Result

- 4개 엔진 어댑터 턴 타임아웃이 모두 `ANYGARDEN_AGENT_<ENGINE>_TURN_TIMEOUT_SEC`로 조정 가능(codex/gemini 신규, claude/openhands 기존 키 보존). supervisor/ping이 턴 타임아웃에서 자동 보정되어 N>600에서도 silent-drop이 방지된다.
- 테스트: agent 패키지 488 passed. 신규 17 + 어댑터 permission 회귀 10 통과. cross-package parity 4건은 cluster 패키지 미설치 환경 아티팩트로 확인(설치 후 11 passed). ruff: 변경 파일 전부 clean(무관한 `profile/loader.py` 기존 lint은 범위 외).
- 후속 #493(per-agent 설정 UI)이 이 헬퍼 위에 per-agent leg + 전파/검증/UI를 얹는다.
