# fix(projects): refuse task status changes from a superseded turn (#795)

- Commit: `7a2df9f`
- Author: Changyong Um
- Date: 2026-10-07
- PR: —

## Situation

2026-10-05 실제 모델 라이브 검증(QA-06 1회차)에서 실행이 멈췄다. 담당자의 질문 턴이 아직 실행 중일 때 사용자가 답했다. 답변 처리는 태스크를 재claim하고 재개 턴을 만들었지만, 직후 옛 질문 턴이 태스크를 `blocked`로 보고했다. 재개 턴은 결과를 제출하지 못했고, 태스크는 `blocked`/`UNKNOWN_FAILURE`로 남았다. 결정적 테스트에는 이 순서가 없었다.

## Task

- 라이브 순서를 모델 없는 테스트로 재현(EXE-18)
- 재개 이후 옛 턴이 태스크 상태를 덮어쓰지 못하게 하기
- 같은 턴 안의 재시도 같은 정상 흐름은 막지 않기

## Action

- `anygarden/mcp/tools.py::mark_task_status`: 실행 태스크에서 lease를 확인한 다음, 같은 `task_id`·같은 담당자에 대해 호출 턴보다 늦게 만들어진 `AgentTurn`이 있으면 `TASK_TURN_SUPERSEDED`로 거부
- `tests/test_project_execution_flow.py`
  - `test_early_answer_still_resumes_task` 두 변형 추가: 옛 턴이 조용히 끝나는 경우, 끝나기 전에 `blocked`를 보고하는 경우
  - 옛 턴의 보고가 거부되는지 직접 확인
  - 하네스에 원본 결과를 돌려주는 `tool_error_or_ok` 추가

## Decisions

- 선택지:
  1. 답변 처리가 옛 턴을 강제로 종료(stop)한다.
  2. `authorize_turn`에서 더 새로운 턴이 있으면 옛 턴의 모든 도구 호출을 거부한다.
  3. 상태를 바꾸는 `mark_task_status`만 막는다.
- 3을 택했다.
  - 1은 실행 중인 네이티브 프로세스를 끊어 진행 중인 출력을 잃게 하고, 정지 확인 프로토콜까지 거쳐야 해서 범위가 크다.
  - 2는 영향 범위가 넓다. 옛 턴이 끝나기 전에 산출물을 읽거나 마무리 보고를 하는 정상 동작까지 막을 수 있다.
  - 이번 결함은 상태 덮어쓰기 하나였으므로 그 지점만 막았다.
- "더 새로운 턴"은 같은 태스크·같은 담당자로 한정했다. 재시도는 같은 턴의 새 attempt라 영향이 없다.
- 재검토 조건: 옛 턴이 다른 도구(질문 재등록, 산출물 발행)로도 재개 턴과 충돌하는 사례가 나오면 2를 다시 검토한다.

## Result

- 답변이 먼저 와도 재개 턴이 태스크를 이어받아 완료할 수 있다. 옛 턴의 `blocked` 보고는 `TASK_TURN_SUPERSEDED`로 거부된다.
- 검증: 새 테스트 중 `blocked` 변형은 수정 전 코드에서 실패하고 수정 후 통과한다. cluster 전체 2,792 passed.
