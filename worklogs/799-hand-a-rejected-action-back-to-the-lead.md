# fix(projects): hand a rejected action back to the lead (#799)

- Commit: `e2204b4`
- Author: Changyong Um
- Date: 2026-10-07
- PR: —

## Situation

2026-10-05 라이브 검증 QA-08 2회차(거절)에서 결함 F3가 나왔다. 사용자가 외부 제출 승인을 거절했다. 운영실에 거절 메시지는 남았지만, 제출 태스크는 `blocked`로 남고 아무 에이전트도 깨어나지 않았다. 이 태스크가 필수 작업이면 실행은 하위 작업을 영원히 기다린다. 태스크 오류도 자유 문장으로 저장돼 공개 코드 `APPROVAL_REJECTED`로 보이지 않았다.

## Task

- 거절 이후 실행이 다음 단계로 넘어갈 주체를 정하기
- 외부 전송이 일어나지 않는다는 보장은 유지하기

## Action

- `project_executions/approvals.py::decide`
  - 거절 시 `task.error = "APPROVAL_REJECTED"`(공개 코드)
  - 결정 기록 후 `reconcile_execution(db, task)`를 불러 기존 하위 결과 통지 경로로 총괄을 깨운다
- `tests/test_project_execution_flow.py`: EXE-20 — 거절 후 총괄이 거절된 태스크 ID와 `APPROVAL_REJECTED`가 담긴 턴을 받는지, 태스크가 `blocked`/`APPROVAL_REJECTED`인지, 외부 전송이 0인지 확인

## Decisions

- 거절 후 무엇을 할지는 제품 결정이다. 사용자가 "모두 진행"을 요청했으므로 가장 보수적인 기본값을 택하고, 이슈에 제안으로 기록했다.
- 선택지:
  1. 제출 담당자를 깨워 대안을 보고하게 한다.
  2. 총괄을 깨운다.
  3. 실행을 자동으로 취소한다.
- 2를 택했다.
  - 실행 전체를 판단할 수 있는 것은 총괄이다.
  - 하위 태스크의 done·failed·blocked를 총괄에게 넘기는 경로(`reconcile_execution`)가 이미 있어서 새 메커니즘이 필요 없다.
  - 3은 사용자 의도(일부만 거절했을 수 있음)를 추측한다.
- 태스크는 `failed`가 아니라 `blocked`로 유지했다. 이후 다른 승인을 다시 요청할 여지를 남기기 위해서다.
- 한계: 통지 이벤트 키가 (태스크, 결과 버전, 상태)로 정해진다. 그래서 같은 결과 버전에서 거절이 두 번 일어나면 두 번째 통지는 생략된다.
- 재검토 조건: 거절 후 동작에 대한 제품 요구가 정해지거나, 거절이 반복되는 흐름이 생기면 이벤트 키와 동작을 다시 본다.

## Result

- 거절 후 총괄이 사유와 함께 깨어나고, 태스크 오류는 `APPROVAL_REJECTED`로 보이며, 외부 전송은 없다.
- 검증: EXE-20 테스트는 수정 전 코드에서 실패하고 수정 후 통과한다. cluster 전체 2,791 passed.
