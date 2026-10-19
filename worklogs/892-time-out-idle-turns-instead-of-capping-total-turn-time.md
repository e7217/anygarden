# fix(agent): time out idle turns instead of capping total turn time (#892)

- Commit: `92034be` (92034be3d3c88f27290363c70841c2c6d36b0669)
- Author: Changyong Um
- Date: 2026-10-19T12:23:47+09:00
- PR: —

## Situation

에이전트 턴 타임아웃은 "턴 시작 후 총 경과 시간" 기준이었다. 그래서 내레이션과 셸 명령이
계속 이어지는 정상 턴도 `turn_timeout_sec`(기본 600s)가 지나면 끊겼다(iac-agent,
10:56→11:06, 출력 없는 원격 `qm clone` 도중). 바깥에도 시간 기준 차단 지점이 여럿 있었다.
supervisor `wait_for`(900s), 턴 시간에서 계산한 WS `ping_timeout`, 서버 orphan sweep(1200s),
그리고 계획서에는 없던 durable turn lease(`turn_timeout_sec + 300s`, 기본 1200s)였다. lease가
만료되면 살아 있는 턴도 재배달됐다.

## Task

- `turn_timeout_sec`를 idle(활동 없음) 한도로 다시 해석한다. 절대 상한은 두지 않는다(사용자 결정).
- 실행 중인 도구가 있는 동안에는 idle로 보지 않는다. 멈춘 도구는 사용자의 Stop으로 끝낸다.
- 오래 걸리는 정상 턴을 시간만으로 끊는 바깥 계층(supervisor·ping·orphan·lease)을 모두 없앤다.
- 결과 코드 `TIMEOUT_STOPPED` / `TIMEOUT_AFTER_SIDE_EFFECT`와 EXE-10 분류는 유지한다.

## Action

- `packages/cluster/anygarden/agent/runtime/execution/turn_clock.py` (신규): `TurnClock`. 이 파일의 동작:
  - `touch`: 활동 시각을 갱신한다.
  - `wrap(emit)`: progress emit마다 touch한다. activity started/completed로 열린 도구 수를 집계한다(thinking 제외, 0 미만 금지).
  - `tool_ended`: completed emit 없이 끝난 도구를 닫는다.
  - `remaining`: 열린 도구가 있으면 +inf를 돌려준다.
- `execution/codex.py`·`pi.py`: 한 번만 계산하던 deadline 루프를 TurnClock으로 바꿨다.
  - clock은 `_TURN_CLOCK` ContextVar로 `_collect`에 넘기고, stdout 줄마다 touch한다.
  - 실패한 파일 편집은 #880 규칙상 completed를 emit하지 않는다. 이 경우 `tool_ended()`로 도구를 닫는다. 이걸 빼면 도구 수가 줄지 않아 idle 판정이 영구히 꺼진다.
  - 타임아웃 시 `execution.idle_timeout` 로그를 남긴다.
- `runtime/handler_wrapper.py`: `wait_for`와 `engine_timeout` 인자를 제거했다. 타임아웃은 안쪽 `EngineTimeoutError` 경로에서만 나고, 기존 notice도 그대로 게시된다.
- `integrations/_turn_timeout.py`: `resolve_supervisor_timeout`과 slack·floor 상수를 제거했다. `resolve_ping_timeout()`은 660s를 반환하고, `ANYGARDEN_AGENT_WS_PING_TIMEOUT_SEC`로 덮어쓸 수 있다.
- `integrations/codex_cli.py`·`room_execution.py`: `register_room_adapter`에서 turn_timeout 인자를 뺐다. pi-cli와 codex-cli 모두 이 함수의 2초 `_typing_loop`를 거치는 것을 확인했다.
- `agent/cli.py`: ping 계산과 `_ENGINE_TIMEOUT_KEY`를 제거했다. ChatClient의 기본값이 고정 ping을 쓴다.
- `agent/client.py`: #190 주석을 갱신했다(원인이었던 SDK는 #506에서 제거됨).
- `scheduler/lifecycle.py`: `sweep_orphaned_requests(..., is_alive=None)`를 추가했다.
  - slow path 후보 중 살아 있는 턴은 건너뛴다.
  - crashed fast path는 is_alive를 보지 않는다.
  - is_alive가 예외를 내면 살아 있지 않은 것으로 본다.
- `turns/service.py`: `recover_stalled_turns(..., is_alive=None)`를 추가했다.
  - lease 만료만이 사유인 attempt가 살아 있으면, 재배달하지 않고 lease를 연장한다.
  - crash·restart deadline·명시적 fence(`attempt.reason`)는 기존대로 처리한다.
  - is_alive는 쓰기 트랜잭션 전에 미리 판정한다.
- `app.py`: `_turn_liveness`를 추가했다. agent→participant(`Participant.room_id/agent_id`)를 조회하고 `app.state.typing_tracker`(TTL 5s)와 대조한다. orphan sweeper와 turn recovery 루프에 전달한다.
- `api/v1/agents.py`: `turn_timeout_sec` 범위를 30..86400으로 바꾸고, orphan 연동 공식을 제거했다.
- 프론트 `OverviewPanel.tsx`와 `i18n/catalogs/admin.ts`: 라벨을 "활동 없음 한도" / "Idle limit"으로 바꾸고, 도움말에 "오래 걸리는 작업은 계속 진행"을 넣었다. 문구만 바꿨다.
- 문서: `CHANGELOG.md`(Changed, 서버 먼저 업그레이드 안내), `docs/requirements.md` ENG-03/ENG-04, `docs/plans/2026-06-23-agent-turn-timeout-design.md`(#892로 일부 대체됨 표시).
- 테스트:
  - `test_turn_clock.py`(신규, 8개).
  - codex·pi 가짜 엔진에 `chatty`·`tool-silent` 시나리오를 추가했다. idle 0.5s에서 1초 넘게 활동해도 완료된다.
  - 도구 실행 중 멈춘 턴은 idle로 끝나지 않고, Stop으로 정리된다.
  - 트리 회수는 도구 없이 멈추는 `stall-tree`로 검증한다.
  - EXE-10은 `effect-stall`·`tool-done-stall`·`failed-edit-stall`로 유지했다.
  - 그 밖에 supervisor 무제한, ping 상수와 env, orphan is_alive 4케이스 + app 콜백, lease 연장·재시도, API 경계(3600·86400 허용, 86401 거부) 테스트를 추가했다.

## Decisions

- **활동의 기준: stdout 줄 + progress emit.** emit 필터를 거치지 않는 이벤트(codex reasoning 등)도 잡는다. 원격 명령은 로컬 CPU·IO 지표로 구분할 수 없으므로 리소스 지표는 쓰지 않았다.
- **열린 도구가 있으면 idle 판정 중지.** 원격 디스크 복제 같은 정상 명령이 통과해야 한다. 별도의 tool_idle 한도는 적절한 값을 미리 알 수 없어 기각했다.
- **바깥 계층은 typing 신호로 생존을 판단(메모리 TypingTracker).** 새 프로토콜이나 스키마가 필요 없다. 서버가 재시작해 tracker가 비어도 오판 방향은 "기존 threshold 동작"이므로, 죽은 턴을 숨기지 않는다.
- **계획 대비 추가: durable turn lease 연장.** 계획 분석에서 빠진 지점이었다. 이것 없이는 20분 무렵 lease가 만료돼 재배달되므로, 이슈의 목표(장시간 정상 턴 유지)를 달성할 수 없었다. orphan과 같은 `is_alive` 콜백을 재사용했다.
- **ping 고정 660s.** 기본 설정에서는 이미 660s였다. `turn_timeout_sec`를 600보다 크게 잡은 에이전트만 ping이 줄어든다. 라이브 확인이 필요하다.
- **`_ENGINE_TIMEOUT_KEY` 제거.** ping 계산에만 쓰였다. 어댑터는 엔진 키를 직접 매핑한다(#500 회귀 테스트도 함께 삭제).
- 재검토 트리거:
  - codex가 실패한 command에 `item.completed`를 보내지 않는 경우(열린 도구가 남아 Stop에 의존하게 됨).
  - typing 루프가 없는 새 엔진 경로가 생기는 경우.
  - 무인 Task 턴에서 반복 루프가 확인되는 경우(budget 적용 범위는 후속).

## Result

- 진행 이벤트가 이어지거나 도구가 실행 중인 턴은 시간만으로 끊기지 않는다. 출력 없이 멈춘 턴만 idle 한도에서 기존 타임아웃 notice와 함께 끝난다.
- 테스트:
  - `uv run pytest -n logical tests/agent`: 826 passed, 2 skipped.
  - cluster 전체 `uv run pytest -n logical`: 3952 passed, 7 skipped.
  - `npm run build`: 통과.
- 새 ruff 위반은 없다(변경 파일 기준. 레포 전체 `ruff check`는 main에서도 기존 위반으로 실패한다).
- 첫 `tests/agent` 실행이 한 번 멈췄다(재실행 시 13초에 통과). 기존 플레이크(#745 계열)로 추정하며, 재현되지 않았다.
- 미확인:
  - 라이브 노드에서 15분 넘는 원격 명령이 끝까지 완료되는지(소켓 유지, orphan·lease 재배달 없음, 답변 게시).
  - `turn_timeout_sec > 600` 에이전트의 ping 660s 안전성.
  - 실제 codex의 실패한 command 이벤트 형태.
