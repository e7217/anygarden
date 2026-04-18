# feat(cluster): surface attached library skills in manifest dialog (#133)

- Commit: `92e2d15` (92e2d15···)
- Author: Changyong Um
- Date: 2026-04-19
- PR: #133 (issue)

## Situation

Admin UI의 `Edit manifest` 다이얼로그(`AgentEditDialog.tsx`)는 `AgentFile` 테이블 로우만 Files 트리에 노출했다. Skills 라이브러리에서 attach한 스킬들은 별도 테이블(`AgentSkill`)에 저장되어 spawn 시점에 `lifecycle._resolve_skill_files()`로 agent workspace에 병합되지만, manifest UI에는 전혀 표시되지 않아 "test-agent에 web-design-guidelines 스킬이 붙어 있는데 manifest에는 AGENTS.md만 있다"는 혼란을 일으켰다. API 측 상태는 `GET /api/v1/admin/skills`가 `attached_agent_ids: list[str]`과 preview 엔드포인트를 이미 제공 중이었다.

## Task

- Files 트리에 "Attached skills" 섹션을 읽기 전용으로 추가해 attach된 라이브러리 스킬의 존재를 가시화
- 클릭 시 SKILL.md 본문을 readonly 에디터에 표시
- 편집/삭제 불가 (라이브러리에서 관리됨)
- Save 로직에 영향 없도록 (기존 `WorkingFile` 흐름 회귀 방지)
- 백엔드 API 변경 없이 기존 admin 엔드포인트 재활용
- 28개 기존 테스트 회귀 없이 신규 기능 단위 테스트 추가

## Action

- `packages/cluster/frontend/src/hooks/useAgents.ts`
  - `AttachedSkill`, `SkillPreview` 인터페이스 추가
  - `fetchAttachedSkills(agentId)` — `/api/v1/admin/skills`에서 `attached_agent_ids.includes(agentId)` 필터링 후 approved 상태만 반환
  - `fetchSkillPreview(skillId)` — `/api/v1/admin/skills/{id}/preview` lazy fetch
  - 훅 반환 객체에 두 함수 추가
- `packages/cluster/frontend/src/components/AgentEditDialog.tsx`
  - Props에 optional `fetchAttachedSkills`, `fetchSkillPreview` 추가 (기존 테스트/호출자 호환)
  - `useNavigate`로 "View in Skills" 링크 구현
  - 새 state: `attachedSkills`, `previewBySkillId`, `selectedAttachedSkillId`, `attachedSkillSection` (collapse)
  - `loadInitial`에서 파일 + 스킬 병렬 fetch
  - `handleSelectAttachedSkill`: 선택 시 `selectedPath` 클리어 + preview lazy 로드 + 캐싱
  - `handleSelectWorkingPath`: tree 선택 시 attached-skill 선택 클리어 (상호 배타)
  - Files 트리 하단에 구분선 + Lock 아이콘과 함께 "Attached skills (N)" 섹션 렌더
  - 우측 에디터에 3-way 분기: `selectedFile` (편집 가능) / `selectedAttachedSkill` (readonly + 배너 + View in Skills) / 없음
- `packages/cluster/frontend/src/components/Sidebar.tsx`, `AdminMachines.tsx` — 새 훅 함수를 `AgentEditDialog`에 전달
- `packages/cluster/frontend/src/components/AgentEditDialog.test.tsx`
  - 모든 테스트를 `MemoryRouter`로 래핑 (`useNavigate` 요구사항)
  - 신규 describe 블록: 빈 목록 시 섹션 미표시, skill 선택 시 lazy fetch 호출 및 readonly textarea 표시 검증

## Decisions

`.tmp/plan-133-manifest-attached-skills.md` 근거로 결정:

**타입 분리: `AttachedSkill`을 `WorkingFile`과 별도 타입으로**
- A. `WorkingFile`에 `readOnly` 플래그 추가 — 통합 단순화
- B. 별도 타입 + 상태 — save 로직 회귀 방지 → **선택**
- C. AgentFile 테이블에 스킬도 주입 (서버 변경) — 범위 외

결정적 근거: `handleSave`가 `files` 배열만 순회해서 PUT/DELETE하는데, 실수로 readonly 스킬이 이 루프에 섞이면 AgentFile 테이블로 덮어쓰기 위험. 타입 분리는 그 경로를 컴파일 타임에 차단.

**데이터 prefetch vs lazy**
- A. 모든 attach된 스킬의 SKILL.md를 open 시 prefetch — 1회 클릭 지연 없음
- B. 클릭 시 preview lazy 요청 + 캐싱 → **선택**

결정적 근거: N개 스킬 prefetch는 5~20KB×N 네트워크 + 메모리. 기존 AdminSkills Preview가 이미 lazy 패턴이라 일관성. `previewBySkillId` 캐시로 두 번째 클릭부터는 즉시 표시.

**섹션 배치: Skills 폴더에 통합 vs 별도 섹션**
- A. 기존 `skills/` 폴더 하위에 readonly 항목 섞기 — 트리 통일
- B. 하단에 구분선 + 별도 섹션 → **선택**

결정적 근거: 편집 가능/불가 항목을 같은 트리에 섞으면 "이건 왜 편집 안 돼?" 혼란. 시각적으로 분리하면 의도가 명확. Lock 아이콘·배너가 readonly 의도 강조.

**View in Skills 링크 구현**
- A. `/admin/skills?skill={id}` 딥링크 — 자동 스크롤/선택 UX
- B. `/admin/skills`로만 이동 → **선택 (범위 한정)**

결정적 근거: AdminSkills 페이지가 현재 쿼리 파라미터를 소비하지 않음. 딥링크 구현은 별도 이슈로 후속. 일반 navigate만으로도 admin이 스킬을 찾아가기 충분.

**Props에 optional로 추가**
- 기존 테스트가 MemoryRouter 없이 렌더했기 때문에 새 필수 props를 추가하면 14건 회귀. Optional로 선언 + 테스트 헬퍼 일괄 업데이트로 점진적 마이그레이션.
- 가정: AgentEditDialog의 다른 호출자는 Sidebar와 AdminMachines 두 곳뿐 (확인됨). 추가 호출자가 생기면 각자 훅에서 새 함수 전달 필요.

가정: `SkillPreviewOut`은 `skill_md` 전체 본문을 제공한다 (서버에서 truncate할 수 있음 — 계획서 L118-122 참조). 현재 구현은 truncation을 사용자에게 경고하지 않음 — 전체가 필요한 admin은 Skills 페이지에서 원본 확인.

## Result

- test-agent에서 Edit manifest 열면 "Attached skills (1)" 섹션에 `web-design-guidelines` 표시
- 스킬 클릭 시 SKILL.md 본문이 readonly textarea에 로드, extra_files가 있으면 경로 목록 렌더
- "View in Skills" 링크 클릭 시 다이얼로그 닫히고 `/admin/skills`로 navigate
- Save 시 `attachedSkills` 관련 네트워크 요청 없음 (기존 파일 upsert/delete 흐름만 실행)
- `cd packages/cluster/frontend && npm run build` 통과 (12340 modules, 타입 에러 없음)
- `npm test` 전체 216건 (22 files) 통과 — AgentEditDialog 기존 26 + 신규 2 = 28건
- 후속 과제: `extra_files` 본문 prefetch (Preview API 확장), AdminSkills 쿼리 파라미터 기반 자동 스크롤
