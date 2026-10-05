# test(projects): cover delegation, questions, approvals, cancel and inbox end to end (#778)

- Commit: `f91abae`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

운영실·서브룸 프로젝트 실행(#778)은 수동 QA(2026-10-01, 실제 codex 모델)로만 검증되어 있었다. 백엔드 pytest가 하나도 없었고, 그 사이 연합 실행 scope 불일치나 오류 레지스트리 누락 같은 결함이 숨어 있었다. QA 시나리오를 반복 가능한 자동 테스트로 옮겨야 main에 머지할 근거가 생긴다.

## Task

- QA-03(위임), 04(맥락 전달), 06(추가 정보), 08(승인), 13(취소)과 인박스 API를 모델 없이 결정적으로 검증
- 프로덕션 보호 장치(턴 lease, 네이티브 시작 허가, 재개 턴 한정 권한)를 우회하지 않고 실제 프로토콜대로 호출
- 각 테스트에 `@req` 요구사항 ID 부여

## Action

`packages/cluster/tests/test_project_execution_flow.py` (신규 7개):
- 하네스: 실제 `ConnectionManager`에 가짜 에이전트 소켓을 `turn_control=True`로 구독 → `deliver_pending_outbox`가 lease 프레임을 전달 → `authorize_native_start`로 시작 허가 → 턴 헤더를 붙여 `/mcp/rpc` 호출. 턴 종료는 `record_lifecycle`의 `skipped`, 중지 신호는 `deliver_pending_stops`로 전달
- 픽스처: 운영실(총괄 lead), 개발 서브룸(worker), 릴리스 서브룸(releaser), owner·outsider 사용자, 모의 제출 대상(`mock-portal`)
- 외부 제출은 `httpx.MockTransport`로 가로채 전송 횟수와 Idempotency-Key를 확인
- 요구사항: EXE-06, 07, 09, 11, 12, 13, INB-01, 02, 03 (로컬 `docs/features.md`에 EXE-11~13 추가)

## Decisions

- 선택지: (1) DB 행을 직접 만들어 서비스 함수를 호출, (2) 실제 턴 전달·시작 허가 프로토콜을 따라가는 하네스, (3) 라이브 스택과 실제 모델.
- (2)를 택했다. (1)은 lease와 시작 허가 같은 보호 장치를 건너뛰어 테스트가 통과해도 실제 런타임 경로를 보증하지 못한다. (3)은 비결정적이고 비싸서 회귀 방지용으로 맞지 않는다.
- 테스트가 처음 예상과 다른 동작을 만난 경우는 코드를 확인한 뒤 기대값을 맞췄다.
  - 태스크 spec이 목표·원 요청·지시를 합쳐 조립된다 → QA-04 맥락 전달의 근거로 단언
  - 같은 question_key에 다른 문장은 거부된다 → 덮어쓰기보다 안전한 규칙으로 판단
  - 질문 상태 `pending`, 인박스 ID `question:` 접두사
- 승인 후 실행은 승인이 만든 재개 턴에서만 허용된다. 테스트도 첫 턴을 끝내고(`skipped`) 재개 턴을 받아 실행해서, 이 보호 장치를 그대로 검증한다.
- 재검토 조건: 턴 정상 완료(`ok`)를 통한 결과 보고 경로(`begin_completion`)는 아직 다루지 않았다. QA-07(결과 취합)·QA-21(완료 보고)을 옮길 때 하네스에 추가해야 한다.

## Result

- 새 테스트 7개 통과. 오류 레지스트리 수정을 되돌리면 EXE-13 테스트가 실패해 결함 검출을 확인했다.
- cluster 전체 2,780 passed, 2 skipped. `req_report.py` 결과 이번 요구사항 9개 모두 pass.
- 남은 일: QA-05·07·12·15~21의 자동화, INB-03의 산출물 403 검증, 종합 시나리오 라이브 실행.
