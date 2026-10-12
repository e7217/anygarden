# feat(bench): requirement coverage gate, live benchmark runner and gap closure

- Commits: `4afddb5` (harness, coverage, first fixes), merges `7710569` (#769) and `e406b69` (#772), `b49c7b9` (remaining gaps), `0d7d6b8` (solo deliverable check), `93fb7ae` (first core-suite results)
- Author: Changyong Um
- Date: 2026-10-12
- PR: (not opened yet)

## Situation

"Anygarden 같은 멀티에이전트 플랫폼은 어떤 벤치마크를 기준으로 삼아야 하나"를 논의하면서 현재 구성을 점검했다. 결정적 테스트는 탄탄했지만 기준 지표로 쓰기에는 빈 곳이 있었다.

- 요구사항 123개 중 `@pytest.mark.req`로 연결된 것은 20개였다. 카탈로그(`docs/features.md`)는 로컬 전용이었다
- 실제 엔진 시나리오는 일회성 `driver.py`로 돌렸고 결과는 git 밖에 있었다. 10월 드라이버는 #802에서 제거된 speaker strategy API를 호출하고 있어 이미 동작하지 않았다
- 단일 에이전트 기준선, 비공개(hold-out) 세트, 성공당 비용, 운영 지표가 없었다

## Task

- 모든 요구사항이 선언한 계층의 테스트로 검증되고, CI가 이를 강제하게 한다
- 실제 엔진 벤치마크를 반복 가능하게 만들고 결과 이력을 남긴다 (pass^k, 허위 완료율, 성공당 비용, 팀 대비 솔로 향상폭)
- 운영 중 협업 상태를 볼 수 있는 지표를 제공한다
- 그 과정에서 드러난 공백과 결함을 닫는다
- 제약: 레포가 public이라 보안 공백 메모가 있는 `features.md`는 커밋하지 않는다

## Action

- 요구사항 커버리지
  - `docs/requirements.md` 신규(추적): `| ID | 요구 | 검증 |`, 검증 값은 `pytest`·`vitest`·`e2e`·`build`·`gap`
  - `scripts/req_report.py`: vitest·Playwright 제목의 `[ID]` 인식, `spec_layers`, `--require-coverage`, Known gaps 출력
  - `.github/workflows/ci.yml`: 각 테스트 단계가 JUnit을 올리고 `Requirement coverage` 잡이 합쳐 판정한다
  - 14개 영역 전체에서 기존 테스트에 태그를 달고 빠진 테스트를 작성했다
- 라이브 벤치마크 `benchmarks/`
  - `runner/`: solo·team 토폴로지 시드, mock 제출 수신기, 시나리오(QA-03/06/08, GT-01..03, 데이터로 정의한 DL-*), 결과 원장, `annotate`, hold-out 디렉터리, 추세 리포트
  - `suite.json`(코어 세트 v1), `README.md`(운영 규칙과 지표 정의)
- 운영 지표: `GET /api/v1/metrics/collaboration`(`observability/collaboration.py`)
- 제품 공백 구현: ENG-09 일반 방 턴 취소, MSG-08 메시지 수정·삭제, ROOM-05 방 나가기, USE-07 예산 관리 화면(+ `GET /api/v1/budgets/overview`), FED-09 토폴로지 peer, SYS-06 백그라운드 업데이트 확인(`update_check_interval_hours`, 0이면 끔), FED-08 `anygarden federation init-credentials`, USE-05/06 예산 하드스톱 연결, EXE-21 담당자 block 시 총괄을 한 번 깨움
- AGT-08(#769), NODE-09(#772) 브랜치를 병합하고 태그를 달았다
- 결함 수정
  - MSG-03: REST 전송이 SQLite writer를 잡지 않아 동시 전송 시 500 → `begin_write_transaction`
  - MSG-07: REST 인터랙션 응답이 잘못된 import로 항상 500
  - 반응 브로드캐스트가 dict를 넘겨 실서버에서 커밋 후 예외 → `ReactionOut` 프레임
  - 외부 동작 권한 없는 실행에 `role="release"` 위임을 받아 영원히 끝나지 않던 문제 → 위임 단계에서 거부 (라이브 DL-01 team에서 발견)
  - 테스트 순서 의존 불안정성: 앱 부팅이 남긴 structlog WARNING 필터가 이후 `capture_logs`를 비움 → conftest에서 테스트마다 초기화

## Decisions

- **카탈로그 위치**
  - 채택: 요구 문장만 담은 `docs/requirements.md`를 새로 추적한다
  - 기각: `features.md`를 커밋. 보안 공백 메모가 public 레포에 노출된다
- **시나리오를 코드로 둘지**
  - 채택: 프로토콜 시나리오는 러너 코드, 산출물 시나리오는 데이터. hold-out은 레포 밖에 같은 형식으로 둔다
  - 기각: 실행마다 드라이버를 새로 작성. API가 바뀌면 조용히 썩는다(실제로 #802 이후 깨져 있었다)
- **release 위임 검증 위치**
  - 채택: 위임 시 거부. `allowed_actions`는 실행 시작 후 바뀌지 않으므로 그 시점에 판단할 수 있고, 총괄이 즉시 `work`로 다시 위임할 수 있다
  - 기각: 완료 시 거부(기존). 작업이 끝날 수 없는 상태로 남는다

## Result

- 요구사항 124개(EXE-21 추가) 모두 통과 테스트 또는 빌드로 검증되고, `gap`은 0개다
- 결정적 검증(한 번의 실행): agent 692, cluster 2,886, vitest 861, 빌드, e2e 45(CI=1), 커버리지 게이트 모두 통과
- 라이브 스모크(codex-cli): DL-01 team은 release 위임 수정 전 15분 정지, 수정 후 1.8분 통과
- 코어 세트 정식 측정(codex-cli `gpt-6.1-sol`, both, k=3): 10개 시나리오 모두 pass^3, 허위 완료 0%, 30회 실행 동안 응답 유실·멈춘 턴·실패 턴 0
  - DL-01 solo 첫 측정은 러너 결함(직접 답변을 완료로 보지 않음)으로 `invalid` 판정, 수정 후 3/3
  - GT-03은 호스트 bwrap 제약으로 `environment` 판정, `trusted`로 3/3
- 남은 과제: DL-01·02는 solo도 통과해 팀 향상폭을 가르지 못한다(team 토큰 7–11×). 분업이 필요한 산출물 시나리오를 추가해야 한다
