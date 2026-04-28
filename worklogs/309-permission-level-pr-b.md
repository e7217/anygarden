# feat(agents): permission level — gemini + claude-code mappings + topology ⚠ + activity (#309 PR-B)

- Date: 2026-04-28
- PR: TBD (from `feat/309-permission-level-pr-b`)
- Stacked on: PR-A (`feat/309-agent-permission-level`, #310)

## Situation

PR-A (#310) 가 schema + REST + spawner + codex 어댑터까지 ship 했지만 다음 4가지가 비어 있었다:
1. **gemini-cli 어댑터** — `permission_level` 인자만 받고 native 다이얼로 변환 안 함 (yolo + skip-trust 가 항상 켜져 있음)
2. **claude-code spawner** — `_CLAUDE_CODE_DEFAULT_SETTINGS` 가 정적 11도구 allow-list 로 고정, tier 무시
3. **토폴로지** — `trusted` 에이전트가 시각적으로 일반 에이전트와 구분 안 됨, 호스트 접근 권한 부여를 한눈에 못 봄
4. **AgentSettingsDialog Activity** — `agent_permission_changed` audit 이벤트가 raw event_type 으로만 표시되어 from/to 정보 가려짐

## Task

- gemini 어댑터에 `_resolve_gemini_flags()` 매핑 + adapter 가 `_call_gemini` 의 cmd 에 동적으로 flag append.
- claude-code spawner 에 tier 별 settings.json 생성 헬퍼 추가, `_materialize_agent_dir` 호출 사이트가 헬퍼를 사용하도록 변경.
- `/api/v1/graph` agent node 페이로드에 `permission_level` 추가, AgentNode 컴포넌트가 `trusted` 시 ⚠ 표식 렌더 (DESIGN.md §2 의 muted warning 색).
- ActivityPanel 의 system events 섹션이 `agent_permission_changed` 행을 `from → to` 형태로 inline 렌더.
- 모든 변경에 단위 테스트 + 회귀 검증.

## Action

### Stage 1 — Gemini 어댑터 매핑 (Phase H1)

- `packages/agent/doorae_agent/integrations/gemini_cli.py` (+45 lines):
  - 모듈 레벨 `_resolve_gemini_flags(permission_level)` — `{approval_yolo, skip_trust}` 딕셔너리 반환. `restricted` 만 둘 다 False, 나머지 (None/standard/trusted) 는 둘 다 True. 알 수 없는 tier 는 `ValueError`.
  - `GeminiCliAdapter.__init__` 시그니처에 `permission_level: str | None = None` 추가, `self._permission_level` 보관.
  - `_call_gemini` 의 cmd 구성을 동적으로 변경 — 정적 4개 인자 list 대신 `--approval-mode yolo` 와 `--skip-trust` 를 flag 에 따라 conditionally append.
  - `integrate_with_gemini_cli(...)` 가 `DOORAE_AGENT_PERMISSION_LEVEL` env 를 읽음 (PR-A 의 codex 패턴 미러).
- `packages/agent/tests/test_gemini_permission_mapping.py` (+45 lines, 5 tests): None/standard/restricted/trusted 매트릭스 + 알 수 없는 tier ValueError.

### Stage 2 — Claude-code spawner 매핑 (Phase H2)

- `packages/machine/doorae_machine/spawner.py` (+50 lines):
  - 새 클래스 상수 `_CLAUDE_CODE_RESTRICTED_SETTINGS` — 5도구만 (Read, Glob, Grep, WebSearch, WebFetch). Bash/Write/Edit/Task/TodoWrite 가 빠져 LLM 이 inspect 만 가능, 셸/쓰기 못 함.
  - 새 classmethod `_claude_code_default_settings(permission_level)` — None/standard/trusted 는 기존 11도구 allow-list, restricted 는 5도구. 알 수 없는 tier `ValueError`.
  - `_materialize_agent_dir` 의 settings.json 생성 사이트가 정적 `_CLAUDE_CODE_DEFAULT_SETTINGS` 대신 `_claude_code_default_settings(msg.permission_level)` 호출. 11도구 default 는 byte-identical 유지 — 기존 admin 의 settings.json diff 회귀 0.
- `packages/machine/tests/test_claude_code_permission_settings.py` (+70 lines, 5 tests): None/standard 가 11도구 매트릭스 정확히 일치, restricted 가 mutator 5종 stripped, trusted ≡ standard, 알 수 없는 tier ValueError.

### Stage 3 — 토폴로지 ⚠ 표식 (Phase H3)

- `packages/cluster/doorae/api/v1/graph.py` (+8 lines): `_build_global_graph` / `_build_personal_graph` 둘 다 agent node `data` 에 `permission_level` 추가. Pre-#309 클라이언트는 모르는 키 무시.
- `packages/cluster/frontend/src/components/topology/nodes/AgentNode.tsx` (+25 lines):
  - `data.permission_level` 추출, `permission_level === 'trusted'` 면 ⚠ marker 노드 렌더.
  - aria-label / title 에 "permission trusted (host access)" suffix 자동 추가 — 스크린 리더 + 툴팁 호환.
- `packages/cluster/frontend/src/components/topology/nodes/AgentNode.css` (+25 lines):
  - `.agent-node__trusted` — top-left 모서리 14×14 원형 marker. 색은 #c2410c (muted warning orange) on #fff7ed (warm white tint). DESIGN.md §2 의 brand-color-is-for-action 원칙 따라 brand blue 회피. position: absolute → 부모 .agent-node 에 position: relative 추가.

### Stage 4 — ActivityPanel system event 렌더 (Phase H4)

- `packages/cluster/frontend/src/components/agent-settings/ActivityPanel.tsx` (+30 lines):
  - system events 섹션의 `<li>` 렌더가 `agent_permission_changed` 시 details 의 `from`/`to` 를 추출해 mono-font badge 로 inline 표시 ("standard → trusted").
  - 다른 system events (start_requested, stop_requested, state_changed) 는 기존 한 줄 포맷 유지.
  - `data-testid="activity-permission-row"` 부착으로 미래 e2e 테스트 정착.

### Stage 5 — 검증

- `npm run build` 8.77s clean.
- `vitest run` 375/375 (회귀 zero).
- `uv run pytest` cluster 867 + machine 328 (323 + 5 new) + agent 318 (313 + 5 new) — 1513/1513 backend tests green.

## Result

- **세 엔진 모두 tier 의미가 일관**:
  - codex: `restricted=read-only` / `standard=workspace-write` / `trusted=danger-full-access` (PR-A)
  - gemini: `restricted=approval+trust prompt` / `standard=trusted=yolo+skip-trust`
  - claude-code: `restricted=5도구 inspect-only` / `standard=trusted=11도구 broad`
- **호스트 접근 가시화** — `trusted` 에이전트는 토폴로지 노드에 ⚠ 표식 + AgentSettingsDialog Activity 에 명시적 "standard → trusted" audit. 사용자가 호스트 권한 부여 상태를 한눈에 인지 가능.
- **회귀 zero** — 모든 default (None, standard) 는 PR-A 와 byte-identical. 기존 row/manifest/settings.json 모두 그대로.
- **확장성 확보** — 세 엔진 모두 tier-flag 매핑 함수 1개에 single source of truth. 미래 SDK 변경 (gemini의 trustedFolders 정책, claude-code 의 새 도구 등) 시 한 곳만 수정.
- **신규 코드량** — 약 354 라인 추가 / 30 라인 제거. 그 중 단위 테스트 115 라인 (32%).

## 후속 (#309 종료 후 별도 이슈로 추적)

- **Default 비대칭 (의도 결정 필요)** — gemini/claude-code 의 standard 는 codex 의 standard 보다 여전히 넓음 (Bash 자유 vs OS sandbox). 이는 의도된 것인가? `standard` 가 모든 엔진을 codex 수준으로 좁힐지에 대한 별도 PR 필요 — 현재 정상 동작하는 기능들이 깨질 수 있어 신중히.
- MCP auto-approve 화이트리스트 per-agent
- working-dir scope (codex `--writable-roots`)
- network egress 정책
- budget / cost limits (#302 Phase 3 와 합쳐짐)
- Engine-specific raw override (jsonb)
