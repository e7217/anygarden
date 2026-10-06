# fix(projects): report a worker's deliberate block as TASK_BLOCKED (#797)

- Commit: `d4351cc`
- Author: Changyong Um
- Date: 2026-10-07
- PR: —

## Situation

2026-10-05 라이브 검증에서 결함 F2가 나왔다. 담당자가 "변경 목록이 없다"는 사유를 적어 태스크를 `blocked`로 표시했는데, 태스크 API·실행 상세·인박스에는 `error: UNKNOWN_FAILURE`로 보였다. 같은 태스크의 recovery 정보는 `TASK_BLOCKED`였다. 그래서 같은 화면 안에서 정보가 어긋났고, 사용자는 정상 대기와 실제 장애를 구분할 수 없었다.

## Task

- 담당자의 의도적 차단을 차단으로 표시하기
- 내부 예외 문구를 공개 필드로 내보내지 않는다는 기존 원칙은 유지하기

## Action

- `project_executions/recovery.py::public_task_error(status, value)`: `blocked` 태스크에 닫힌 코드가 아닌 사유가 있으면 `TASK_BLOCKED`를 돌려준다. 그 밖에는 기존 `public_reason_code`와 같다.
- 적용 위치: `api/v1/tasks.py`의 태스크 출력, `project_executions/serialization.py`의 실행 상세 태스크 행, `project_executions/inbox.py`의 인박스 태스크
- `tests/test_project_execution_flow.py`: EXE-19 — 세 경로 모두 `TASK_BLOCKED`이고, 원문이 공개 error 필드에 노출되지 않는지 확인

## Decisions

- 선택지:
  1. 담당자 사유 원문을 공개 error 필드로 그대로 노출한다.
  2. 원문용 필드(`blocked_reason`)를 새로 만든다.
  3. 코드만 `TASK_BLOCKED`로 바로잡는다.
- 3을 택했다.
  - 1은 `task.error`에 시스템 예외 문구가 들어갈 수 있어 출처를 구분할 수 없다. 그러면 누출 방지 원칙이 깨진다.
  - 2는 출처를 기록하는 스키마 변경이 필요하다. 반면 담당자의 설명은 이미 작업 메시지와 결과에 남는다.
- 재검토 조건: 사용자가 인박스에서 차단 사유 원문을 바로 봐야 한다는 요구가 생기면, 출처 플래그와 함께 2를 구현한다.

## Result

- 의도적 차단이 `TASK_BLOCKED`로 일관되게 보인다.
- 검증: EXE-19 테스트는 수정 전 코드에서 실패하고 수정 후 통과한다. cluster 전체 2,791 passed.
