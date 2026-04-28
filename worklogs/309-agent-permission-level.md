# feat(agents): per-agent permission level (3-tier) + codex sandbox dial (#309 PR-A)

- Date: 2026-04-28
- PR: TBD (from `feat/309-agent-permission-level`)

## Situation

세 엔진(codex / gemini-cli / claude-code) 의 권한 다이얼이 doorae 안에서 하드코드 + 비대칭으로 존재했다. codex 만 OS-level seatbelt/landlock 으로 `workspace-write` 샌드박스를 강제하고, gemini-cli (`--approval-mode yolo`) 와 claude-code (`bypassPermissions` + 11도구 allow) 는 사실상 호스트 자유 접근. 사용자가 "codex 가 호스트 정보를 못 가져온다" 고 보고한 현상의 근본 원인이 OS 샌드박스이고, 동시에 doorae 의 기본 동작이 codex < gemini ≈ claude-code 로 보안 의도 비일관 상태였다. UI/API 어디서도 권한을 조정할 메커니즘이 부재하여 코드 수정 외 우회가 불가능했다.

## Task

- doorae 레벨에서 **`permission_level: restricted | standard | trusted`** 라는 3-tier 의미적 추상화 도입.
- 각 엔진 어댑터가 native 다이얼(codex `sandbox` + `approval_policy`; gemini `--approval-mode`; claude-code allow-list) 로 번역.
- DB 컬럼 + REST API + admin-only PATCH + audit log + 머신 spawner 패스스루 + codex 어댑터 매핑 + AgentSettingsDialog UI 까지 **end-to-end 한 PR (PR-A)**.
- gemini/claude 매핑 + 토폴로지 ⚠ 표식은 PR-B 로 분리하여 회귀 격리.
- Default 동작 보존: `NULL` → `standard` 매핑 (= 기존 codex 하드코드와 동일). 마이그레이션 backfill 불필요.

## Action

### Stage 1 — DB 모델 + 마이그레이션 (Phase A)

- `packages/cluster/doorae/db/models.py` (+10 lines): `Agent.permission_level: Mapped[Optional[str]]` 컬럼, `model` 옆자리. nullable + default None — `restricted/standard/trusted` 외엔 어댑터 측에서 거부.
- `packages/cluster/doorae/db/migrations/versions/038_agent_permission_level.py` (+44 lines, new): `op.batch_alter_table("agents")` 로 컬럼 추가 + downgrade 로 drop. server_default 없음 → 기존 row 가 NULL 로 남고 어댑터의 `_resolve_codex_flags(None) = standard` 매핑이 처리.
- `packages/machine/doorae_machine/protocol/frames.py` (+8 lines): `SyncDesiredStateFrame.permission_level: str | None = None`. pre-#309 머신/서버는 미지의 필드를 무시 → 호환.
- `packages/cluster/doorae/scheduler/lifecycle.py` (+5 lines): `_build_sync_frame` 에서 `agent.permission_level` 을 frame dict 에 매핑.
- `packages/cluster/tests/test_migrations.py`: head assertion `037` → `038` (5 spots).
- 검증: `uv run alembic upgrade head && downgrade -1 && upgrade head` clean.

### Stage 2 — REST API + audit log (Phase B)

- `packages/cluster/doorae/api/v1/agents.py` (+45 lines):
  - `AgentUpdate.permission_level: Optional[str] = Field(default=None, pattern="^(restricted|standard|trusted)$")` + `_set: bool = False` 패턴 (reasoning_effort 미러).
  - `AgentOut.permission_level: Optional[str]` 노출 (`from_attributes=True` 라 자동 매핑).
  - PUT `/agents/{id}` 핸들러 (이미 `get_admin_identity` dependency 라 admin-only):
    - `permission_level_set` 시 prev/new 비교 후 변경되면 `ActivityLog(event_type='agent_permission_changed', details={'from', 'to', 'by_user_id'})` 추가.
    - 무변화 시 ActivityLog skip — 반복 PATCH 시 audit 폭주 방지.
- `packages/cluster/tests/test_agents_api.py` (+165 lines, 7 tests):
  - default NULL / admin set / admin clear / invalid 422 / non-admin 403 / audit row 생성 / 무변화 시 audit no-op.
  - PATCH → PUT 메서드 통일 (전체 `.patch(` → `.put(` sed-replace; doorae 의 agent update 는 PUT 으로 노출).
- 검증: 7/7 새 테스트 + 기존 860 + 1 = 867 cluster tests green.

### Stage 3 — Spawner 환경변수 전파 (Phase C)

- `packages/machine/doorae_machine/spawner.py` (+15 lines):
  - `SpawnManifest.permission_level: str | None = None` 필드.
  - 자식 프로세스 env 구성 시 `env["DOORAE_AGENT_PERMISSION_LEVEL"] = msg.permission_level or "standard"` — 무조건 set 해서 어댑터가 absent-key 특수 케이스를 처리할 필요 없게 함. `None` 은 spawner 가 standard 로 변환.
- `packages/machine/doorae_machine/daemon.py` (+5 lines): `_spawn_with_token` 의 SpawnManifest 생성에서 `permission_level=getattr(manifest, "permission_level", None)` 로 패스스루. `getattr` 은 in-memory 매니페스트의 pre-#309 호환을 위함.
- `manifest_store.py`: 손대지 않음 — Pydantic frame 직렬화가 새 nullable 필드를 자동 처리.
- 검증: 323/323 machine tests green.

### Stage 4 — Codex 어댑터 매핑 (Phase D, TDD)

- `packages/agent/tests/test_codex_permission_mapping.py` (+50 lines, new, 5 tests, **Red 먼저 작성**):
  - `None` → standard 매핑
  - `standard` → `(workspace-write, never)` (pre-#309 동작)
  - `restricted` → `(read-only, untrusted)` — codex 의 default approval prompts 를 살려 silent 권한 상승 차단
  - `trusted` → `(danger-full-access, never)` — 호스트 명령 실행 + 묻지 않음 (사용자가 명시적으로 트러스트 부여)
  - 알 수 없는 tier → ValueError (`"godmode"` 같은 typo 가 silent 다운그레이드되지 않게)
- `packages/agent/doorae_agent/integrations/codex.py` (+50 lines, **Green**):
  - 모듈 레벨 `_CODEX_TIER_FLAGS: dict[str, tuple[str, str]]` — single source of truth, codex SDK 변경 시 한 곳만 수정.
  - `_resolve_codex_flags(permission_level)` pure 함수.
  - `CodexAdapter.__init__` 시그니처에 `permission_level: str | None = None`, `approval_policy: str | None = None` 추가. `permission_level` 이 set 되면 `_resolve_codex_flags` 호출하여 self._sandbox / self._approval_policy 결정. 명시적 sandbox/approval_policy 만 받는 직접 호출 호환 유지.
  - `start_thread` 의 `ThreadStartOptions(approval_policy=..., sandbox=...)` 가 hardcoded `"never"` / `"workspace-write"` 대신 `self._approval_policy` / `self._sandbox` 참조.
  - `logger.info("codex.thread_created", ...)` 로그도 동적 dial 에 맞춰 update.
- `integrate_with_codex(...)` 에 `permission_level` 인자 추가. 인자 없으면 `os.environ.get("DOORAE_AGENT_PERMISSION_LEVEL")` 읽음 — `cli.py` 는 변경 없이 spawner 의 env 통해 자동 적용.
- 검증: 5/5 신규 테스트 + 기존 308 = 313 agent tests green.

### Stage 5 — Frontend UI (Phase E)

- `packages/cluster/frontend/src/hooks/useAgents.ts` (+5 lines): `Agent.permission_level?: 'restricted' | 'standard' | 'trusted' | null` literal union.
- `packages/cluster/frontend/src/components/agent-settings/OverviewPanel.tsx` (+45 lines):
  - `updateAgent` patch 타입에 `permission_level` + `permission_level_set` 추가.
  - `handlePermissionLevelChange` — `handleReasoningChange` 패턴 미러. `'' → null`, 무변화 short-circuit, configError surface.
  - `<dt>Permission</dt><dd><select>` 행을 reasoning select 다음에 mount. 4개 옵션:
    - `Default (standard)`
    - `Restricted — read-only`
    - `Standard — workspace only`
    - `⚠ Trusted — host access`
  - `trusted` 선택 시 inline 작은 caption: "호스트 정보·명령 접근 가능. 신중히 사용하세요." (data-testid 부착으로 PR-B 의 토폴로지 표식과 testID 일관성 확보).
- 검증: `npm run build` 8.85s clean (tsc 포함), `vitest run` 375/375 (회귀 zero).

### Stage 6 — 수동 검증 + commit

- 모든 백엔드 1503 tests + 프론트 375 tests green.
- `uv run alembic` 마이그레이션 round-trip 안전.
- `git status` 깨끗, 변경 13 파일 + 신규 2 파일.

## Result

- **codex 호스트 접근 즉시 가능** — admin 이 OverviewPanel 에서 codex 에이전트의 Permission 을 `Trusted` 로 변경하면 다음 spawn 부터 `sandbox=danger-full-access`, `approval_policy=never` 로 시작. 사용자가 보고한 "호스트 정보를 못 가져온다" 현상이 한 번의 PATCH 로 해결됨.
- **반대 방향도 가능** — 신뢰가 약한 에이전트를 `Restricted` 로 좁혀 `read-only` 샌드박스 + `untrusted` approval 로 잠글 수 있음. PR-B 가 들어오면 gemini/claude 도 같은 tier 의미로 좁아짐 (현재 PR-A 단독은 codex 만 약화).
- **회귀 zero** — 기존 row 모두 `permission_level=NULL` → 어댑터의 `_resolve_codex_flags(None) = ("workspace-write", "never")` 로 pre-#309 동작 byte-identical 유지. 마이그레이션 backfill 불필요.
- **Audit trail 확보** — 모든 권한 전환이 `ActivityLog` 에 from/to/by_user_id 와 함께 기록. 보안 리뷰 시 누가 언제 무엇을 elevate 했나 추적 가능.
- **확장성 확보** — `_CODEX_TIER_FLAGS` 가 single source of truth 라 codex SDK 의 향후 approval_policy 라벨 변경 (`untrusted` → `on_request` 등) 시 한 줄 수정으로 끝. PR-B 의 gemini/claude 매핑도 같은 패턴.
- **신규 코드량** — 약 503 라인 추가 / 10 라인 제거. 그 중 단위 테스트 215 라인 (43%). 신규 외부 dep 없음.

## 후속 (#309 PR-B)

- gemini-cli 어댑터 — `restricted` 시 `--approval-mode default` (yolo 해제) + `--skip-trust` 제거 검토
- claude-code spawner — `restricted` 시 `_CLAUDE_CODE_DEFAULT_SETTINGS.permissions.allow` 를 `[Read, Glob, Grep, WebSearch, WebFetch]` 로 축소
- 토폴로지 노드 (`AgentNode.tsx`) 에 `permission_level === 'trusted'` 시 ⚠ 표식
- AgentSettingsDialog Activity 섹션에 `permission_changed` 이벤트 표시
- Default 동작의 비대칭 (gemini/claude 가 codex 보다 넓은 호스트 접근권 가짐) 결정 — `standard` 가 모든 엔진을 codex 수준으로 좁힐지 / 현 동작 유지할지
