# fix(ui): 전체 페이지 점검에서 발견된 UI 결함 모음 (#773)

- Commit: `be670ef`..`7230746` (7 commits on `fix/773-ui-audit-defects`)
- Author: Changyong Um
- Date: 2026-10-01
- PR: #773 (issue)

## Situation

2026-10-01 247 배포(v0.19.0)의 전체 페이지를 점검하고(스크린샷 122장) 사용자 화면의 결함 7건을 찾았다. 모두 main에서 미수정 상태였다. 공통점은 두 가지다:
- 백엔드 식별자나 upstream 오류 원문이 화면에 그대로 노출된다
- 비활성·빈 상태가 오류나 빈 화면처럼 보인다

## Task

- 알 수 없는 경로가 빈 흰 화면으로 뜬다
- 메시지 검색 모달이 Esc로 닫히지 않고, 다이얼로그 role과 focus trap이 없다
- skills.sh 검색창을 열자마자 400 원문이 노출된다
- 사용량 페이지의 에이전트별 목록이 UUID로 표시된다
- 룸 편집 창의 에이전트별 토큰 표에 사람 참가자가 UUID로 나온다
- 연합 페이지가 `PEERING_DISABLED`(503)를 "상태 확인 불가"로 표시한다
- 사소한 것: 시스템 페이지의 "최신" 표기 오류, `DialogDescription` 누락, 프로젝트 메뉴의 한국어 라벨
- 제약: API는 선택 필드만 추가해 하위 호환을 지키고, UI는 DESIGN.md를 따른다

## Action

- `be670ef` — `pages/NotFoundPage.tsx` 신규, `App.tsx`에 `path="*"` 라우트 추가, `common.notFound*`/`common.goHome` i18n
- `d21c6c1` — `components/SearchDialog.tsx`를 공용 Radix `Dialog`로 교체(sr-only 제목·설명, 입력에 초기 포커스), 쓰이지 않게 된 `chat.closeSearch` 제거
- `5f10153` — 스킬 검색
  - 백엔드 `api/v1/skills.py`: 2자 미만 쿼리는 upstream을 호출하지 않고 `[]`를 반환한다(`MIN_SEARCH_QUERY_LENGTH`)
  - 프론트 `AdminSkills.tsx`: 열 때 검색하지 않고, 2자 미만이면 버튼을 비활성화하며 안내 문구를 보여 준다. 실패는 읽기 쉬운 문구와 `role="alert"`로 표시하고 upstream detail은 노출하지 않는다
- `e1fefb2` — 사용량
  - 백엔드 `api/v1/usage.py`: `UsageBucket.label` 추가, by_agent 버킷에 `Agent.name`을 채운다
  - 프론트 `UsageSection.tsx`: `label`을 표시하고 id는 툴팁으로 남긴다
- `fe1c902` — `RoomEditDialog.tsx` 토큰 패널에서 `agent_name`이 없는 행(사람 참가자)을 제외한다
- `c4641f4` — `useFederation.ts`가 `PEERING_DISABLED`를 `disabled`로 매핑한다. 비활성 배너 문구를 실제 원인(노드 peer 인증서)에 맞게 고쳤다
- `7230746` — 사소한 항목
  - `AdminSystem.tsx`: "최신" 대신 "PyPI"로 표기하고, 실행 중 빌드가 PyPI보다 새로우면 "PyPI보다 최신" 배지를 단다
  - `Sidebar.tsx`의 다이얼로그 3개와 `GuestRoomPage.tsx` 1개에 `DialogDescription`을 추가했다
  - 한국어 `chat.projectActions`를 "프로젝트 메뉴"로 바꿨다
- 테스트
  - 신규: `NotFoundPage.test.tsx`, `SearchDialog.test.tsx`, `AdminSkills.test.tsx`, `AdminSystem.test.tsx`
  - 추가: `UsageSection.test.tsx`, `RoomEditDialog.test.tsx`, `useFederation.test.tsx`
  - 백엔드: `test_skills_library_stale_and_search.py`, `test_usage_api.py`

## Decisions

- **브랜치와 커밋 단위**: 한 브랜치에 항목별 커밋으로 묶었다. 항목마다 10~50줄이고 파일이 거의 겹치지 않는다. 사용자가 이슈를 2건(크래시 #772 / UI 모음 #773)으로 나누기로 결정했다.
- **룸 토큰 표 — 계획에서 방향을 바꿈**: 계획(.tmp/plan-773)은 백엔드 `token_stats`에서 사람 메시지를 빼는 안을 택했다. 그런데 구현 중에 docstring이 사람 행을 `agent_name=None`으로 넣는 것을 의도된 계약("caller can distinguish")으로 명시하고 있음을 확인했다. 그래서 API는 그대로 두고, 표 제목이 "에이전트별"인 패널 쪽에서 걸러냈다. 다른 소비자는 없지만 계약을 바꿀 이유도 없었다.
- **검색 모달**: Esc 핸들러만 덧붙이는 대신 Radix로 교체했다. Esc만으로는 role·focus trap·스크롤 잠금이 계속 빠지고, 다른 모든 다이얼로그와 동작이 달라진다.
- **스킬 검색의 짧은 쿼리 방어**: 프론트와 백엔드 양쪽에서 막는다. 프론트만 막으면 다른 클라이언트에서 여전히 502가 나고, 백엔드만 막으면 사용자는 열자마자 빈 결과를 보게 된다.
- **시스템 페이지 버전 판정**: 버전 비교는 서버의 `update_available`만 믿고, 프론트에서 semver를 파싱하지 않는다. "업데이트 없음인데 버전 문자열이 다르면 PyPI보다 새로운 빌드"로 판정한다.
- **프로젝트 메뉴 라벨**: 영어 "Project actions"는 적절하다. 한국어 "프로젝트 작업"만 앱의 "작업(Task)" 기능과 혼동되어 이것만 바꿨다.
- **가정**
  - 247에서 UUID로 보이던 토큰 표 행은 사람 참가자다. Participant는 에이전트가 삭제되면 CASCADE로 함께 지워지기 때문이다.
  - 연합 서비스 두 개(`peer_service`, `channel_service`)는 모두 peer 인증서가 있어야 시작된다(`app.py` `_compose_federation_services`).

## Result

- 위 7개 화면을 수정했다. 로컬 서버(워크트리 빌드, 스크래치 DB)에서 브라우저로 확인한 결과:
  - 404 페이지가 뜬다
  - 검색 모달이 Esc로 닫힌다
  - 스킬 검색창이 열릴 때 호출이 없고 안내 문구가 보인다
  - 시스템 페이지에 "PyPI" 라벨이 표시된다
  - 연합 페이지에 비활성 배너가 뜬다
  - 375px 다크 모드에서 가로 넘침이 없다
- 사용량 이름 표시와 룸 토큰 표는 데이터가 필요해 단위 테스트로만 검증했다.
- cluster 전체 2678 passed / 2 skipped, 프론트 vitest 804 passed, `npm run build` 성공.
