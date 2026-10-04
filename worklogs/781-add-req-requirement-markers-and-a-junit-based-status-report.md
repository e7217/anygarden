# test: add @req requirement markers and a JUnit-based status report (#781)

- Commit: `f93f34b`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

"정상 동작"을 문서로만 정의하면 코드와 따로 낡았다. 348개 항목이 하나도 체크되지 않은 채 퇴역 엔진을 기준으로 남아 있던 verification-checklist가 그 예다. 기능 인벤토리 초안(요구사항 ID 약 120개)을 만든 뒤, 요구사항 상태를 손으로 관리하지 않고 테스트 결과에서 얻을 방법이 필요했다.

## Task

- 테스트가 검증하는 요구사항 ID를 코드에 표시
- 추가 의존성 없이 결과를 요구사항 단위로 집계
- xdist 병렬 실행(CI의 `-n auto`)에서도 동작

## Action

- `packages/{cluster,agent}/pyproject.toml`: `req(*ids)` 마커 등록
- `packages/{cluster,agent}/tests/conftest.py`: `pytest_collection_modifyitems`가 마커 ID를 `item.user_properties`의 `req` 속성으로 기록 → JUnit XML `<property name="req">`
- `scripts/req_report.py` (표준 라이브러리만 사용): JUnit 파일들을 읽어 ID별 `pass`/`fail`/`skipped`/`untested` 표를 출력. `--spec`을 주면 Markdown 표 첫 열의 ID 중 테스트가 없는 것을 `untested`로 표시. 실패가 있으면 exit 1
- `packages/cluster/tests/test_req_report.py`: 수집, 상태 집계, 스펙 대조, 종료 코드 테스트 10개
- `CONTRIBUTING.md`: 사용법 추가

## Decisions

- 선택지: (1) StrictDoc·Sphinx-Needs 같은 요구사항 추적 도구, (2) 별도 pytest 플러그인 패키지, (3) conftest 훅 + JUnit 속성 + 작은 스크립트.
- (3)을 택했다. 추적 도구는 안전 인증용이라 이 규모에 무겁고, 플러그인 패키지는 배포 단위가 하나 늘어난다. JUnit `user_properties`는 pytest와 xdist가 기본 지원해 추가 의존성이 없다.
- 스크립트는 표준 라이브러리만 써서 workspace 환경 없이 CI나 로컬 어디서든 실행되게 했다.
- 스펙 ID는 표 첫 열에 있는 것만 인정한다. 본문 언급까지 잡으면 오탐이 생긴다.
- 재검토 조건: Playwright 테스트도 같은 표에 넣어야 하면 Playwright JSON 리포터 파서를 추가한다(범위 밖으로 남김).

## Result

- `@pytest.mark.req("ID")`를 붙이면 요구사항 상태가 테스트 결과로 집계된다.
- 검증: 새 테스트 10개 통과. 임시 테스트로 `-n 2` 실행 → JUnit → 리포트까지 끝단 동작 확인(통과·실패·exit 1). agent 테스트 680개 수집 정상.
- 아직 마커를 붙인 실제 테스트는 없다. #782·#783에서 첫 적용 예정.
