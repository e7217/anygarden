# feat(cluster): approve workflow + audit log for skill library (#125)

- Commit: `37e923b` (37e923b13c080e908ed22208c6207ebd5cb0ceb0)
- Author: Changyong Um
- Date: 2026-04-19T02:08:23+09:00
- PR: #125

## Situation

Phase 1 (#121 / #122) 과 Phase 3 (#127) 로 skill library 가 실용 상태가 됐지만,
register 된 skill 은 즉시 attach 가 가능해 "누가 이 skill 의 body 를 승인했는가"
를 감사할 수 없었다. 내부 팀 운영 체제에서는 OK 지만 다수 admin / 외부 기여자
/ 컴플라이언스 요구가 생기면 gate 가 필요.

`skill_library.approved_by` 컬럼은 Phase 1 migration 018 에 nullable 로 이미
준비되어 있었지만 활성 로직이 없어 값이 항상 NULL 이었다.

## Task

- `approved_by` 를 실제 승인 권한의 signal 로 활성화.
- `approved_at` 컬럼 + `skill_library_audits` 테이블 신규 (register / approve /
  reject / delete / attach / detach 전부 기록).
- resolve gate: 미승인 skill 은 spawn frame 에 포함시키지 않음 + warn.
- attach gate: 미승인 skill attach 요청은 409.
- 기존 Phase 1 등록 데이터는 grandfather — data migration 으로 first admin 이
  approve 한 것으로 스탬프.
- Admin UI: 대기 / 승인 / 거부 탭, status badge, approve / reject 버튼,
  preview 다이얼로그 (SKILL.md 본문 + extra_files 파일 트리), audit drawer.
- 기존 Phase 1 테스트는 "approve 완료 상태" 를 전제로 돌아야 함 — fixture 에
  helper 추가.

## Action

- `packages/cluster/doorae/db/migrations/versions/020_skill_approve_and_audit.py`
  신규. `skill_library.approved_at` 추가, `skill_library_audits` 테이블 생성
  (UUID PK, skill_library_id FK `ondelete=SET NULL`, actor_user_id FK
  `ondelete=SET NULL`, action String(32), detail JSON, at UtcDateTime).
  data migration: 기존 skill row 에 first admin 의 id 를 `approved_by` 로 채움
  + audit "grandfathered" 엔트리 삽입 (admin 없으면 no-op 으로 안전 종료).
- `db/models.py`: `SkillLibraryEntry.approved_at`, `SkillLibraryAudit` 모델.
- `skills_library/service.py`:
  - `approve(skill_id, actor_user_id)` / `reject(...)`: `approved_by`/
    `approved_at` 세팅 + audit 기록. approve 시 attached agents bump 재사용
    (Phase 1 fix #122 경로).
  - `resolve_for_agent` 에 `approved_by IS NOT NULL` 필터 + 미승인 attach
    감지 시 structlog warn.
  - `register` / `delete` / `attach` / `detach` 경로에서 audit row insert.
  - `get_status` / `list_with_status` / `list_audits` 신규 조회 헬퍼.
- `api/v1/skills.py`:
  - `POST /admin/skills/:id/approve`, `POST /admin/skills/:id/reject`.
  - `GET /admin/skills?status=pending|approved|rejected` 필터.
  - `GET /admin/skills/:id/preview` (SKILL.md + extra_files 목록).
  - `GET /admin/skills/:id/audits`.
  - `POST /admin/skills/:id/attach` 에 미승인이면 409 + detail.
  - `SkillOut` 에 `status` 필드 추가.
- `scheduler/lifecycle.py`: `_build_sync_frame` 의 skill merge 부분을
  `_resolve_skill_files` 헬퍼로 추출 → service.resolve_for_agent 에 위임 →
  approve 필터 자동 적용. 미승인 attached 있으면 structlog warn.
- `frontend/src/components/AdminSkills.tsx`: 3 탭 (대기/승인/거부) + status
  badge + approve / reject / preview / history 버튼 + preview 다이얼로그
  (SKILL.md 본문 + extra_files 트리 표시) + audit history drawer.
- 테스트: service 10 케이스 신규 (approve / reject / resolve 필터 / audit
  기록 / grandfather 경로). API 10 케이스 신규 (상태 필터 / attach 409 /
  audit 조회 / preview 응답). lifecycle 2 케이스 신규. 기존 API/service
  fixture 는 `_register_and_approve` helper 로 "approve 된 상태" 를 일관
  생성 → 회귀 없음.

## Decisions

plan `.tmp/plan-125-skill-approve-workflow.md` §3.2 의 결정을 그대로 적용.

- **A1 (resolve + attach 이중 gate)** — attach 에서 409 로 막고, 우회된
  경우에도 resolve 에서 filter + warn. 방어-깊이.
- **B1 (별도 `skill_library_audits` 테이블)** — activity_logs 재사용은 skill
  전용 쿼리 복잡화, 별도 테이블이 Phase 5 stale check 와도 쿼리 경계
  분리 정합.
- **C (migration 번호)** — 현재 head 019 (MCP catalog) → 020 사용. Phase 3
  (#127) 는 migration 추가 없이 병합됐으므로 번호 공간 명확.
- **D1 (기존 Phase 1 스킬 자동 grandfather)** — Phase 2 배포 순간 attached
  agent 가 skill 을 잃지 않도록. audit log 에 "grandfathered" 엔트리로
  추적성 유지.

**Round 1 (#127) 결과 반영 확인** — plan ⚠️ 섹션 두 포인트:

- `content_hash` 가 canonical-tree 해시로 확정되어 audit `detail.before_hash`
  / `after_hash` 는 `SkillLibraryEntry.content_hash` 를 그대로 저장하면 OK.
  plan 의 재검토 필요 사항 해소.
- `extra_files` 가 실제로 body 포함 → preview 다이얼로그에 파일 트리 표시
  가능. 이번 구현에서 SKILL.md 본문 + extra_files 목록을 함께 표시.

**가정** — first admin 이 존재 (data migration 조건). 없으면 no-op 으로
안전 종료 후 admin 등록 시 Phase 2 신규 approve flow 로 수동 처리. 대부분
의 프로덕션 환경은 admin 이 최소 1 명 있음.

## Result

- `uv run pytest` (cluster) — 529 passed (baseline 493 + 약 36 신규/수정).
  1 deselected (pre-existing slow E2E). 머신/에이전트 무관계.
- `uv run ruff check` 변경 10 개 파일 clean.
- `npm run build` (frontend) — tsc + vite 정상 (chunk size warning 은 pre-
  existing).
- alembic upgrade / downgrade -1 / upgrade head sqlite 라운드트립 정상.
  data migration 검증 테스트 포함.
- 동작: admin 등록 → status=pending → 대기 탭 → approve 버튼 → 승인 탭
  이동 → attach 가능 → spawn frame 에 반영. reject 는 거부 탭 / 미승인
  attach 시 409.
- Phase 5 (#126) 가 의존하는 approve gate 의미론이 확정됨 — refresh 후
  재승인 요구 여부는 Phase 5 plan §3.2 B 에서 "B3 (옵션화)" 채택하면 됨.
