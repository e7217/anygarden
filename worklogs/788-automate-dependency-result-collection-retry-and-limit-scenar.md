# test(projects): automate dependency, result collection, retry and limit scenarios (#788)

- Commit: `c4cb943`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

#778에서 QA-03·04·06·08·13과 인박스를 모델 없는 pytest로 옮겼다. 남은 프로젝트 실행 시나리오는 여전히 2026-10-01 수동 QA 기록에만 의존하고 있었다. 결과 취합(QA-07)과 완료 보고(QA-21)는 턴 종료와 결과 전달 경로가 필요해서 첫 하네스로는 다룰 수 없었다.

## Task

- QA-05, 07/21, 12, 20과 INB-03의 산출물 403을 같은 하네스로 자동화
- 하네스를 실제 런타임 프로토콜(종료 영수증, 에이전트 실행 상태)에 맞게 넓히기

## Action

`packages/cluster/tests/test_project_execution_flow.py`:
- 하네스 확장
  - 턴마다 `local_execution_id`를 기억
  - `end_turn(outcome="failed")`가 런타임의 종료 영수증(`native_process_state=finished`, `native_outcome=failed`, `MODEL_EXECUTION_FAILED`)을 함께 보냄
  - 에이전트를 `actual_state="running"`으로 둠
- 새 테스트 6개
  - 의존 작업 대기와 선행 결과 전달 (EXE-01, EXE-14)
  - 산출물 비멤버 차단 (INB-03)
  - 계획 확정 → 하위 결과 수신 → 완료와 최종 보고, 완료 후 추가 호출 거부 (EXE-08, EXE-15)
  - 실패 턴의 사용자 재시도, 멱등성, 외부인 차단 (EXE-10, EXE-16)
  - 위임 한도와 미지원 권한 선언 거부 (EXE-17)

## Decisions

테스트를 쓰면서 처음 예상과 다른 동작을 다섯 번 만났다. 모두 코드를 확인했고, 결함이 아니라 의도된 안전장치였다. 각각 테스트를 실제 흐름에 맞췄다.
- 완료 전에 필수 작업 목록 확정(`seal_project_plan`)이 필요하다. 이 확정은 하위 결과가 도착하기 전의 계획 턴에서 해야 한다. 결과가 도착하면 이전 계획 턴은 무효가 된다.
- 완료된 실행은 모든 도구 호출을 거부한다(`EXECUTION_NOT_ACTIVE`). 따라서 반복 완료로 보고를 바꿀 수 없다.
- 재시도는 종료 영수증이 없으면 `PROCESS_OUTCOME_UNKNOWN`으로 거부된다. 외부 효과가 중복될 수 있기 때문이다.
- 재시도 전에 에이전트가 실제로 실행 중인지 확인한다(`AGENT_STOPPED`).
- 하위 결과가 도착하면 총괄의 계획 턴이 무효가 된다(위 첫 항목과 같은 메커니즘).

범위에서 뺀 것: QA-15·16·17·18·19는 목표 스케줄러와 QA 수정 라운드를 거치는 긴 흐름이라 #788에 남겼다. 정기 실행 중복 방지(QA-17)는 기존 goals 테스트가 상당 부분 다룬다.

재검토 조건: 하네스가 쓰는 `skipped` 종료는 응답 없는 종료를 뜻한다. 응답 메시지를 통한 정상 완료(`begin_completion`) 경로를 검증하려면 하네스를 더 넓혀야 한다.

## Result

- 새 테스트 6개, 파일 전체 13개 통과. cluster 전체 2,790 passed.
- `req_report.py` 기준 테스트로 검증되는 요구사항 22개 모두 pass.
