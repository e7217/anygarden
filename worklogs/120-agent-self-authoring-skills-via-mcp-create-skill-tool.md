# feat(cluster): agent self-authoring skills via MCP create_skill tool (#120)

- Commit: `50085ea` (50085eaee239aaf18fd1692d9905182fec2ca743)
- Author: Changyong Um
- Date: 2026-04-19T02:05:31+09:00
- PR: #120

## Situation

Phase 1-3 skill library 는 admin-only 쓰기 경로였다. 에이전트가 작업하면서
발견한 패턴 / 경험을 skill 로 codify 해두고 재사용하고 싶어도, 매번 admin 이
수동으로 register 해야 해서 에이전트의 학습 성과가 영속화되지 않았다.

또한 "skill 로 저장" 은 컨텍스트 효율 이점 (스킬 메타만 상시 로드, 본문은
on-demand) 이 커서, 에이전트가 긴 메모리에 쌓아두는 방식보다 토큰 절감
효과가 크다.

## Task

- cluster 에 HTTP MCP 서버 노출. FastAPI 위에 JSON-RPC 핸들러 마운트.
- 4 개 도구: `create_skill(name, description, body, extra_files?)`,
  `update_skill(id, body, extra_files?)`, `list_my_skills()`, `delete_my_skill(id)`.
- 인증: `DOORAE_TOKEN` → 호출자 `agent_id` 추출. admin gate 재사용 X —
  agent-identity 전용 dependency.
- DB: `skill_library.created_by_agent_id` nullable 컬럼 추가. 기존 admin-
  registered 스킬은 NULL.
- ownership: service 는 `created_by_agent_id == caller` 만 update/delete
  허용. 타 agent 스킬은 조작 불가.
- spawn gate: `approved_by IS NULL` 이어도 `created_by_agent_id == spawn
  대상 agent.id` 면 resolve 에 포함 (자기 스킬은 self-approve). Phase 2
  approve gate (#125) 와 공존.
- admin UI: AdminSkills 에 agent-authored 필터 + "promote" 버튼 (created_by
  를 NULL 로 설정해 공유 라이브러리로 이관).
- 기존 Phase 1-3 admin register 경로는 그대로 — agent 도구가 쓰는 것은
  별개의 create path.

## Action

### MCP 서버 (신규)

- `packages/cluster/doorae/mcp/__init__.py`
- `packages/cluster/doorae/mcp/auth.py` — `get_agent_identity` dependency.
  `Authorization: Bearer <DOORAE_TOKEN>` 에서 agent 식별.
- `packages/cluster/doorae/mcp/router.py` — `POST /mcp/rpc` JSON-RPC 2.0
  엔드포인트. 메서드: `initialize`, `tools/list`, `tools/call`. 외부 MCP
  SDK 의존성 0.
- `packages/cluster/doorae/mcp/tools.py` — 4 개 도구 구현 (schema + handler).

### Backend 수정

- `db/migrations/versions/021_agent_authored_skills.py` — `skill_library`
  에 `created_by_agent_id` nullable FK 추가 (`ondelete=SET NULL`,
  explicit constraint name for SQLite batch 호환).
- `db/models.py` — `SkillLibraryEntry.created_by_agent_id`.
- `skills_library/service.py` — `create_from_agent`, `update_by_owner`,
  `list_by_owner`, `delete_by_owner`. `resolve_for_agent` 에서 "자기가
  만든 스킬은 approve 여부 무관 포함" 예외 분기.
- `api/v1/skills.py` — admin list 응답에 `created_by_agent_id` 노출 +
  promote 엔드포인트 (`POST /admin/skills/:id/promote` — created_by 를
  NULL 로 세팅 + audit 기록은 Phase 2 에 맡김).
- `app.py` — MCP 라우터 include + `app.state.mcp_skill_handler` 초기화.

### Frontend

- `frontend/src/components/AdminSkills.tsx` — "agent-authored" 필터 토글 +
  promote 버튼 (카드별).

### 테스트 (+ 20+ 케이스)

- `tests/test_mcp_server_create_skill.py` — HTTP MCP 경유 4 개 도구 각각
  + 타 agent 스킬 수정 거부 + agent token 인증 실패 케이스.
- `tests/test_skill_library_agent_authored.py` — service 레벨 ownership +
  resolve 에서 자기 스킬 self-approve 경로.
- `tests/test_migrations.py` — head 019 → 021 (data migration 없으므로
  `down_revision="019"` 로 시작, main merge 시 rebase 로 020 기반으로
  조정됨).

## Decisions

plan `.tmp/plan-120-agent-mcp-create-skill.md` §3 의 결정을 따랐다.

- **접근 A (MCP 도구) 채택** — B (workspace staging) 은 "완료 signal"
  미해결, C (CLI parsing) 는 엔진별 fragility. MCP 가 standard 인터페이스
  + 엔진들이 이미 MCP client 를 내장.
- **사용 경로 무변경** — 스킬 "사용" 은 여전히 기존 Skill 도구 + 파일시스템
  기반. MCP 는 **쓰기 전용**. 에이전트가 도구로 create 하면 다음 spawn 부터
  본인이 사용 가능.
- **auto-approve 로직** — 본인이 만든 스킬은 본인 에이전트에만 attach 되
  므로 gate 우회 허용. 다른 에이전트와 공유하려면 admin 이 promote 해야 함.
- **의존성 0 MCP 구현** — 플랜 §2.5 는 외부 MCP SDK 사용 옵션을 열어뒀으나
  JSON-RPC 2.0 over HTTP 는 직접 구현이 훨씬 단순. `initialize` / `tools/list`
  / `tools/call` 세 메서드만 구현.

### Plan 과의 차이

- **SSE 대신 단일 POST endpoint** — plan §2.5 는 `GET /mcp/sse` + `POST /mcp/messages`
  SSE 트랜스포트를 가정했으나 실측 결과 스킬 쓰기는 request-response 하나로
  충분 (streaming 불필요). `POST /mcp/rpc` 단일 엔드포인트로 단순화.
  엔진들이 표준 JSON-RPC 응답을 수용.
- **manifest composer auto-injection 미구현** — plan §4.2.9 의 "에이전트
  생성 시 자동으로 doorae-skills MCP 서버를 `.claude/settings.json` 등에
  inject" 는 이번 범위에서 제외. 대신 admin 이 #124 MCP template catalog 로
  수동 attach 가능. 후속 이슈에서 연결 고려.
- **description 필드 별도 저장 안 함** — `create_skill(description=...)` 은
  받지만 DB 에 별도 컬럼 추가하지 않고 `list_my_skills` 응답에는 SKILL.md
  첫 줄을 proxy 로 반환. YAGNI.
- **Migration down_revision** — subagent 작성 시점 main head 는 019 라 021
  의 `down_revision="019"` 로 시작. main 에 Phase 2 (#125, 020) 가 먼저
  머지되면 본 브랜치 rebase 시 `down_revision="020"` 으로 조정 필요.

**가정** — Phase 2 (#125) 가 먼저 머지됨을 가정. 아닐 경우 순서 뒤집어
이 plan 의 `approved_by IS NULL` 예외 분기 로직이 noop 이 되고 `approved_by`
자체가 NULL 인 상태로 동작 (Phase 2 전). 기능적 영향 없음.

## Result

- `uv run pytest` (cluster) — 525 passed + 1 deselected. machine 232 passed.
  agent 131 passed + 1 pre-existing unrelated failure (openai key 필요).
- `uv run ruff check` 변경 파일 clean.
- `npm run build` (frontend) 정상.
- 동작: 에이전트가 `POST /mcp/rpc` 로 tools/call create_skill → DB 에
  `created_by_agent_id=<self>, approved_by=NULL` 로 저장 → 다음 spawn 시
  `resolve_for_agent` 가 자기 스킬이라 포함 → `~/.doorae/agents/<id>/skills/<name>/SKILL.md`
  materialize. admin 이 AdminSkills "agent-authored" 필터 → promote 버튼 →
  공유 라이브러리로 이관 (created_by NULL).
- E2E 실기 확인은 병합 후 main 에서 수행 예정.
