# feat(cluster): MCP server template catalog with builtin + custom (#124)

- Commit: `d4956df` (d4956dfcc56dda781300522bb6574d909f52c27f)
- Author: Changyong Um
- Date: 2026-04-19T01:32:32+09:00
- PR: #124

## Situation

에이전트가 github / slack / notion 같은 외부 MCP 도구를 쓰려면 admin 이
`.claude/settings.json` 또는 `.codex/config.toml` 을 raw 편집해서 매니페스트
에 직접 넣어야 했다. 같은 도구를 N 에이전트에 붙이면 N 번 복붙되고, 사내
전용 MCP 서버도 통일된 관리 수단이 없었다. 결과적으로 "기술적으론 가능하나
운영 불가" 상태 — doorae 의 핵심 가치 중 하나인 "에이전트가 실제 유용한
도구를 쓸 수 있다" 가 실현되지 못했다.

## Task

- MCP 서버 템플릿 카탈로그를 cluster 에 추가. 템플릿 ↔ 인스턴스 2단 구조로
  같은 템플릿을 N 에이전트에 부담 없이 attach.
- github / slack / notion / linear / filesystem 5 개 builtin 템플릿이
  cluster 초기화 시 seed.
- admin 이 custom 템플릿 authoring 가능 (raw config snippet + required env).
- credential 은 대칭 암호화 (Fernet) 로 DB 에 저장, env var 로 키 주입.
- 엔진별 (claude-code / codex / gemini-cli) manifest 포맷 차이를
  `config_per_engine` dict 로 흡수.
- spawn 시 `_build_sync_frame` 에서 attached instance 를 엔진별 settings
  파일로 overlay — 기존 AgentFile 경로를 건드리지 않음.
- 프론트엔드 admin 페이지 + 에이전트 attach UI.
- 머신 패키지 / 에이전트 패키지 변경 없음.

## Action

### Backend 신규 모듈

- `packages/cluster/doorae/mcp_templates/__init__.py`
- `packages/cluster/doorae/mcp_templates/encryption.py` — Fernet 기반
  `MCPSecrets` 클래스. env `DOORAE_MCP_SECRETS_KEY` 로 키 주입. dev 모드
  에선 ephemeral 키 + warn 로그 (test/dev 부팅 호환).
- `packages/cluster/doorae/mcp_templates/builtin.py` — 5 개 builtin
  템플릿 정의 + `seed_builtin_templates(db)` 헬퍼. lifespan 에서 호출,
  idempotent upsert.
- `packages/cluster/doorae/mcp_templates/merge.py` — 엔진별 config
  포맷터. claude-code / codex / gemini-cli 각자 JSON / TOML 구조로 렌더.
- `packages/cluster/doorae/mcp_templates/service.py` — template CRUD,
  instance attach/detach, manifest 주입 헬퍼 (lifecycle 에서 호출).
- `packages/cluster/doorae/api/v1/mcp_templates.py` — REST 라우터.
  `get_admin_identity` gate. CRUD + agent attach/detach.
- `packages/cluster/doorae/db/migrations/versions/019_mcp_templates.py` —
  `mcp_server_templates`, `mcp_server_instances` 테이블 생성. UUID PK,
  JSON 컬럼, FK `ondelete=CASCADE`. SQLite 호환 (`op.batch_alter_table`
  필요 없음 — 단순 create_table).

### Backend 수정

- `doorae/db/models.py` — `MCPServerTemplate`, `MCPServerInstance` 추가.
- `doorae/config.py` — `DooraeSettings.mcp_secrets_key: str = ""`.
- `doorae/app.py` — 라우터 include, lifespan 에서 `MCPSecrets` 초기화 +
  `TemplateService` 를 `app.state.mcp_template_service` 에 세팅 +
  builtin seed.
- `doorae/scheduler/lifecycle.py` — `_build_sync_frame` 의 files_map
  빌드 직후 `mcp_template_service.render_instance_files(agent_id)` 호출
  로 엔진별 settings 파일 overlay. AgentFile 과 경로 충돌 시 AgentFile
  우선 (setdefault).
- `pyproject.toml` — `cryptography>=43` 직접 의존성 pin.

### Frontend

- `frontend/src/pages/AdminMCPTemplatesPage.tsx` — sidebar + 컨테이너.
- `frontend/src/components/AdminMCPTemplates.tsx` — 카탈로그 목록,
  custom template 에디터, per-agent attach 다이얼로그 (agent 선택 +
  credential 필드 입력). DESIGN.md warm neutral 팔레트 준수.
- `frontend/src/App.tsx` — `/admin/mcp-templates` 라우트 + AdminRoute.
- `frontend/src/components/Sidebar.tsx` — Admin 섹션에 "MCP Templates"
  링크 추가 (기존 Skills / Machines 밑).

### 테스트 (+37)

- `tests/test_mcp_templates_encryption.py` — Fernet round-trip, dev 모드
  ephemeral 키 경고 로그.
- `tests/test_mcp_templates_merge.py` — 3 엔진 × 2 템플릿 조합 렌더.
- `tests/test_mcp_templates_crud.py` — API CRUD + validation.
- `tests/test_mcp_templates_lifecycle.py` — spawn frame 에 주입된 결과.
- `tests/test_mcp_templates_builtin_seed.py` — startup seed idempotence.
- `tests/conftest.py` — 기본 `mcp_secrets_key` fixture 추가.
- `tests/test_migrations.py` — head revision 018 → 019.

## Decisions

원본 `.tmp/plan-124-mcp-server-template-catalog.md` §3 결정을 대부분 따랐다.

- **템플릿-인스턴스 2단 구조** (plan A1): 하나의 템플릿이 N 에이전트에
  부착되므로 body 중복 없이 공유. 인스턴스 row 는 template_id FK + env
  overrides + attached_agent_id 만.
- **config_per_engine JSON dict** (plan B1): 엔진별 서로 다른 manifest
  포맷을 하나의 row 로 표현. 엔진 추가 시 config 한 키만 추가.
- **Fernet 대칭 암호화** (plan C1): admin 입력 credential 을 DB 평문
  저장 금지. 키는 환경변수로 주입 (DOORAE_MCP_SECRETS_KEY).
- **spawn 시 overlay** (plan D1): `AgentFile` 테이블을 건드리지 않고
  lifecycle 단에서 MCP 설정을 files_map 에 합쳐 머신에 전달. 머신 코드
  한 줄도 안 바꿈.

**계획 대비 조정**:

- **migration 번호** — plan 은 020 가정했으나 현재 main head 가 018 이라
  다음 번호 019 사용. Phase 3 (#123) 가 같은 base 에서 migration 추가
  없이 병합되므로 충돌 없음.
- **dev 모드 MCP_SECRETS_KEY 폴백** — 원래 plan 은 키 없으면 loud fail.
  실제로는 기존 전체 테스트 스위트 (DooraeSettings 를 config 없이
  instantiate) 가 줄줄이 깨지므로 "dev 모드 → ephemeral 키 + warn" 로
  완화. 프로덕션 attach 경로에선 키 미스매치 시 decryption 이 명시 예외로
  실패하므로 plan 의 "loud fail" 목적은 유지.
- **per-agent attach UI** — plan 은 AdminMachines 의 agent 상세 화면에
  별도 탭을 추가하는 안이었으나, 카탈로그 페이지의 "Attach" 버튼 →
  다이얼로그 (agent picker + credential 입력) 로 일원화. 플로우 중복
  회피 + 동일 DESIGN.md 스타일.
- **builtin 템플릿 5 개** — plan 과 일치 (github / slack / notion /
  linear / filesystem). 모든 3 엔진에 동일 command/args/env 지원.

**가정** — `DOORAE_MCP_SECRETS_KEY` 가 프로덕션에서는 반드시 주입된다
(systemd/Docker env). 키가 없으면 기존 암호문이 복호화 불가 — 키 회전은
migration path 로 별도 설계 필요.

**위반 시 재검토** — 키 회전 요구 등장 시 이중 키 지원 (old + new) 으로
마이그레이션 설계. 인스턴스 수가 많아져 JSON 컬럼이 커지면 별도
`mcp_instance_env` 테이블로 분리 검토.

## Result

- `uv run pytest` — 493 passed (신규 37 + 기존 456). 1 deselected (기존
  `slow` E2E). 머신/에이전트 패키지 영향 없음.
- `uv run ruff check` 변경/신규 파일 clean. 기존 repo 의 unrelated 94
  경고는 손대지 않음.
- `npm run build` (frontend) 성공 — tsc + vite.
- Migration round-trip (upgrade / downgrade -1 / upgrade head) sqlite
  기준 정상.
- 동작: admin 이 `/admin/mcp-templates` 접근 → builtin 5 개가 seed 되어
  있음 → "Attach" 다이얼로그로 에이전트 선택 + GITHUB_TOKEN 등 입력 →
  spawn 시 해당 엔진 settings 파일에 설정 오버레이.
- 수동 E2E (실제 GitHub MCP 서버 연결 후 에이전트 질의 응답) 는 병합 후
  main 에서 확인.
