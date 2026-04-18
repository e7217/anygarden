# feat(cluster): skill library with GitHub-based registration (#119)

- Commit: `b3615fe` (b3615fe6044487d7b7099179243063234b2e4876)
- Author: Changyong Um
- Date: 2026-04-19T00:11:26+09:00
- PR: #119

## Situation

스킬을 에이전트 간에 공유하려면 admin 이 매 에이전트마다 SKILL.md 본문을
manifest 에 직접 붙여넣어야 했다. N 명의 admin 이 같은 스킬을 제각기 복붙하면
동기화 부담이 선형으로 커지고, upstream 이 업데이트될 때 어느 에이전트가
최신인지 추적할 방법이 없었다. skills.sh 생태계는 GitHub 레포 기반이라 한
번 등록 + pinned SHA + content hash 만 있으면 공유 / 재현성 / drift 감지를
일관되게 제공할 수 있다. 이슈 #119 는 그 중 Phase 1 (SKILL.md-only MVP) 을
범위로 한다 — approve workflow (Phase 2), 전체 파일 투입 (Phase 3),
skills.sh 검색 프록시 (Phase 5) 는 후속 이슈로 분리.

## Task

- Python 환경만으로 동작하는 GitHub-backed skill library 를 구축한다
  (Node.js skills CLI 의존 금지 — 배포 복잡도 +100MB 와 텔레메트리 우회 목적).
- DB 스킴: (source, name, pinned_rev) 유니크 + agents M:N. body 중복 저장
  없이 여러 에이전트가 같은 skill row 를 참조.
- spawn path 는 네트워크 독립 — 등록 시점에 commit SHA 를 DB 에 pin.
- admin 전용 REST 엔드포인트 + 프론트엔드 admin 페이지.
- 자식 spawner / agent_dir 는 한 줄도 건드리지 않는다 (skill 레이어는
  lifecycle.files_map 에 투입만 하면 됨).
- 테스트는 전부 offline — httpx.MockTransport / fake fetcher 로 네트워크 의존 0.

## Action

- `packages/cluster/doorae/db/migrations/versions/018_skill_library.py`
  신규. `skill_library` (UUID PK, source/name/pinned_rev UniqueConstraint,
  extra_files / scripts_detected JSON, approved_by nullable, content_hash)
  + `agent_skills` M:N 테이블 (composite PK + `ondelete=CASCADE`).
  `ix_skill_library_source_name`, `ix_agent_skills_skill` 인덱스.
  `op.batch_alter_table` 대신 `create_table` — SQLite/Postgres 공용.
- `packages/cluster/doorae/db/models.py:465-540` — `SkillLibraryEntry`,
  `AgentSkill` SQLAlchemy 모델. UUID `default=_uuid`, JSON default=
  `dict`/`list` 로 DB NOT NULL 과 동기화.
- `packages/cluster/doorae/skills_library/github_fetcher.py` 신규 —
  httpx 로 `api.github.com/repos/<source>/git/trees/<rev>?recursive=1` →
  `raw.githubusercontent.com/<source>/<sha>/skills/<name>/SKILL.md` 2-step
  fetch. 예외 계층 `GitHubFetchError` / `SkillNotFoundError` /
  `GitHubRateLimitError` — rate limit 은 403 + `X-RateLimit-Remaining: 0`
  시그니처로 감지. `SkillFetchResult` dataclass 가 `(commit_sha, skill_md,
  scripts_detected)` 묶음.
- `packages/cluster/doorae/skills_library/service.py` 신규 —
  `SkillLibraryService.register()` 는 fetcher 호출 후 `(source, name,
  commit_sha)` 키로 upsert, `content_hash = sha256(skill_md)`. `attach` /
  `detach` / `resolve_for_agent` — resolve 는 AgentSkill ↔ SkillLibraryEntry
  조인으로 `{skills/<name>/SKILL.md: body}` 반환.
- `packages/cluster/doorae/api/v1/skills.py` 신규 —
  `/api/v1/admin/skills` 라우터. POST register, GET list (attached_agent_ids
  포함), DELETE skill (CASCADE), POST/DELETE attach. 모든 엔드포인트
  `get_admin_identity` gate.
- `packages/cluster/doorae/app.py:22-26, 248-256, 345` — skills 라우터
  import + lifespan 에서 `app.state.skill_library_service` 기본 초기화
  (기본 fetcher = network, 테스트는 미리 주입해 override).
- `packages/cluster/doorae/scheduler/lifecycle.py:18-22, 336-356` —
  `_build_sync_frame` 에서 attached skill 을 조인으로 로드해
  `files_map.setdefault(path, body)` 로 merge. `setdefault` 이므로 같은
  경로의 AgentFile 이 있으면 admin 오버라이드가 우선.
- `packages/cluster/frontend/src/components/AdminSkills.tsx` 신규 —
  목록 + register 다이얼로그 (source/name/rev) + attach multiselect +
  delete. DESIGN.md warm neutral 팔레트 / `--color-border` / shadow-whisper /
  shadcn-ui Button/Dialog/Badge 조합으로 AdminMachines 스타일과 일치.
- `packages/cluster/frontend/src/pages/AdminSkillsPage.tsx` 신규 —
  Sidebar + SidebarExpandButton + 모바일 top bar. AdminMachinesPage 패턴 복제.
- `packages/cluster/frontend/src/App.tsx:9-10, 58-59` —
  `/admin/skills` 라우트 + AdminRoute 게이팅.
- `packages/cluster/frontend/src/components/Sidebar.tsx:27, 709-720` —
  Admin 섹션에 BookOpen 아이콘으로 Skills 링크 추가.
- 테스트 — `test_skills_library_github.py` (5: 정상/SKILL.md 부재/
  rate limit/404/raw 404), `test_skills_library_service.py` (7: register
  upsert/pinned_rev 분기/resolve/attach idempotent/detach), `test_skills_library_api.py`
  (7: admin gate/CRUD/attach 404), `test_lifecycle_skills.py` (3: merge/
  AgentFile 우선순위). `test_migrations.py` head revision 017 → 018
  업데이트.

## Decisions

원본 계획 `.tmp/plan-119-skills-library-end-to-end.md` §3.2 의 결정들을
그대로 따랐다.

- **Node.js skills CLI subprocess vs Python httpx 직접** — httpx 채택.
  실측해보면 skills CLI 가 하는 일은 git-trees + raw 두 번의 HTTP 호출
  뿐이었고, Node.js 런타임 추가는 배포 이미지 +100MB 와 원치 않는
  텔레메트리 (`add-skill.vercel.sh/t?skills=<CSV>`) 를 동반한다. 같은
  엔드포인트를 Python 에서 직접 호출하면 동등 기능 + 의존성 0.
- **default revision 전략** — 등록 시 commit SHA 로 pin (re-pin 은 admin
  수동). HEAD 동적 추적은 매 spawn 마다 네트워크 의존 + rate-limit 위험
  + upstream 변조 즉시 반영 (위험). 재현성 확보가 압도적으로 중요.
- **extra_files 스키마** — JSON 컬럼 하나로 `{rel_path: body}` 저장
  (Phase 3 부터). 별도 `skill_files` 테이블은 row 당 diff 가 필요 없는
  원자 단위 데이터라 join 비용만 증가. Phase 1 에서는 `{}` 비워 두고
  `scripts_detected` 에 경로 목록만 UI metadata 로 남긴다.
- **spawn 시 body 주입 위치** — AgentFile 로 복제 저장 X, lifecycle 에서
  동적 merge O. skill 재사용이 이 feature 의 주 목적인데 N 에이전트면 body 도
  N 번 복제되는 쪽은 그 가치를 무효화한다. 메모리·DB 비용 차이가 크고,
  "library 는 library 로 유지, 에이전트별 오버라이드는 AgentFile" 이라는
  책임 분리도 분명.
- **per-skill scripts disable 토글** — 제거 (사용자 결정 2026-04-18).
  "등록 = 그 스킬의 모든 파일 신뢰" 가 일관된 trust 모델. 일부만 빼고
  싶으면 별도 스킬로 분리하거나 admin 이 등록을 안 하면 된다. 토글로
  복잡도를 추가하지 않는다.
- **자식 컴포넌트 미수정** — spawner / agent_dir 의 확장자 화이트리스트는
  #112 에서 이미 `.py/.sh/.js/.ts/.mjs` 까지 확장됐고 SKILL.md 는 기본 `.md`
  허용 범위. skill 레이어는 lifecycle.files_map 한 곳에만 주입하면 되므로
  spawner 는 물론 machine 패키지도 한 줄도 안 건드렸다.

**가정** — (1) admin 이 등록하는 레포는 공개 레포. private 은
`GITHUB_TOKEN` env 로 Phase 5 에서 지원 예정. (2) 스킬 디렉토리 규약이
`skills/<name>/SKILL.md` 라는 것. 이 규약을 따르지 않는 레포 (예:
github/awesome-copilot) 는 Phase 1 에서 거부 — SkillNotFoundError 로 명확한
에러 노출. (3) Phase 1 은 approval gate 없음 — 내부 admin 운영 체제라
등록 즉시 attach 가능. Phase 2 에서 approval + audit log 로 공식화.

**위반 시 재검토 트리거** — (a) 레포 layout 다양성이 늘어 Phase 3 이
`extra_files` 로 실제 파일을 투입할 때 SKILL.md 가 root 에 있거나 다른
prefix (patterns/) 인 경우가 많아지면 fetcher 파서 확장 필요. (b) admin
수동 등록이 하루 60+ 건을 넘어가면 GitHub 익명 rate limit 에 막힘 —
`GITHUB_TOKEN` 우선 지원. (c) skill body 가 수 MB 를 넘는 스킬이 등장하면
DB / 메모리 부하 대응 필요 — Phase 3 에서 per-file 1MB, total 10MB 제한
예정.

## Result

- 백엔드 pytest: 435 통과 (신규 22 — github 5 / service 7 / api 7 /
  lifecycle 3). `alembic upgrade head / downgrade -1 / upgrade head`
  라운드트립 정상.
- 프론트엔드 vitest: 22 파일 / 214 테스트 통과 (회귀 없음).
  `npm run build` (tsc + vite) 통과.
- ruff 신규 파일 전부 clean (기존 app.py 의 기존 경고는 범위 밖).
- admin 워크플로: `/admin/skills` 에서 GitHub 레포 등록 → 목록에
  pinned SHA + scripts count 표시 → attach 다이얼로그에서 에이전트
  체크박스로 toggle → 다음 spawn 시 `skills/<name>/SKILL.md` 가 에이전트
  디스크에 materialize 됨.
- 시각적 / E2E 스모크 (실제 `vercel-labs/agent-skills` 등록 + 에이전트
  응답 확인) 는 워크트리 환경 제약상 미수행 — 병합 후 main 에서 확인 예정.
- Phase 2 (approve workflow), Phase 3 (full directory passthrough),
  Phase 5 (skills.sh 검색 프록시 + stale check) 는 별도 이슈로 진행.
