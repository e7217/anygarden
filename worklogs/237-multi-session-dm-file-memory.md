# feat(rooms,agent,machine,frontend): per-agent multi-session DM + cross-engine file memory + ephemeral mode (#237)

- PR: #237
- Date: 2026-04-22
- Branch: `feat/237-multi-session-dm-file-memory`

## Situation

에이전트 DM은 "유저 × 에이전트" 당 Room 1개로 고정돼 있었다. 대화가 길어질수록 Claude Agent SDK / Codex thread / Gemini CLI 세션이 무한히 누적되면서:

- **비용·지연 증가**: 세션 토큰 수가 선형적으로 커짐.
- **품질 저하**: 긴 context에서 에이전트가 초기 맥락을 흘림.
- **주제 분리 불가**: 사용자는 "지금 대화는 잠깐 테스트만 하고 싶다"거나 "업무 A와 업무 B를 섞고 싶지 않다"를 표현할 수단이 없었음.

runtime context 압축(rolling summary, tiered memory) 방향도 검토했지만, 3개 엔진(Claude Code / Codex / Gemini CLI) 모두에서 동작할 공통 메커니즘이 없었고 요약 품질 리스크가 컸다. ChatGPT/Claude.ai가 쓰는 **두 축 분할**을 채택:

1. 대화 경계를 DM 단위로 나눠 SDK 세션을 cold-start.
2. 장기 기억은 파일(`memory/notes.md`)에 기록해 다음 세션 시작 때 system_prompt로 주입.

## Task

다음 9개 페이즈를 한 PR에 묶어서 구현:

1. DB 스키마(`rooms.ephemeral`, `agents.memory_md`) + alembic 029/030.
2. REST API: room PATCH `ephemeral`, list filter `representative_agent_id`, `GET/PATCH agents/{id}`의 `memory_md`, `POST agents/{id}/dms` (per-agent 새 DM 생성).
3. WS welcome frame에 `ephemeral`/`memory_md` 추가 + `room_settings_changed` 프레임에 `ephemeral` 추가.
4. Agent runtime 공통 helper `compose_memory_block` + 3개 어댑터(Claude Code / Codex / Gemini CLI)의 system_prompt 조립점 통합.
5. Machine spawner가 DB 스냅샷 → `memory/notes.md` 마테리얼라이즈, daemon이 파일 변경 감지 후 `agent_memory_update` 프레임으로 sync-back.
6. AGENTS.md 템플릿의 Memory 섹션을 신규 `memory/notes.md` 규칙으로 교체.
7. Frontend 사이드바 Agents 섹션을 "에이전트 → DM[] 트리"로 리팩터 + per-user localStorage 접힘 상태.
8. Room 헤더에 ephemeral 토글(EyeOff/Eye 아이콘, Notion Blue accent).
9. 워크로그 + README 수준의 문서화.

## Action

### Phase 1 — DB 스키마

- `packages/cluster/doorae/db/migrations/versions/029_room_ephemeral.py`: `rooms.ephemeral BOOLEAN NOT NULL DEFAULT 0`. SQLite `batch_alter_table` + `server_default=0` (기존 022/023 패턴).
- `packages/cluster/doorae/db/migrations/versions/030_agent_memory_md.py`: `agents.memory_md TEXT NULL`. 신규 에이전트는 빈 memory로 시작.
- `db/models.py`의 `Room` / `Agent`에 대응 필드 + docstring 추가.
- `tests/test_migrations.py`: 기대 head revision `"028"` → `"030"` 업데이트 (4곳 sed).

### Phase 2 — 백엔드 API

- `rooms/router.py::RoomOut`에 `ephemeral` 필드 추가. `list_rooms`에 `representative_agent_id` 쿼리 파라미터 추가 → 에이전트별 DM 목록 조회 지원.
- `RoomUpdate`에 `ephemeral: bool | None` 추가. PATCH 권한: 일반 방은 admin-only, DM은 **owner(참가자 user)** 허용. 권한 체크는 핸들러 내부 인라인 게이트(rename 경로를 여전히 열어두기 위함 — `context_window_enabled` 패턴과 동일).
- `api/v1/agents.py`:
  - `create_agent`의 자동 DM 생성 시 `representative_agent_id=agent.id` 스탬프 (Phase 2의 DM 필터가 동작하려면 필수).
  - `AgentUpdate`/`AgentOut`에 `memory_md` + `memory_md_set` 추가 (기존 `agents_md_set` idiom).
  - 신규 `GET /api/v1/agents/{id}` (admin-only, 단일 에이전트 read).
  - 신규 `POST /api/v1/agents/{id}/dms` (admin-only, 새 DM 생성 + owner 역할로 caller 참가 + `ensure_agent_in_room` + `lifecycle.on_room_added`). 이름 미지정 시 `DM: <agent.name> #<N>` 자동 생성.

### Phase 3 — WS 프로토콜

- `ws/protocol.py::WelcomeOut`에 `ephemeral: bool = False`, `memory_md: str | None = None` 추가.
- `RoomSettingsChangedOut`에 `ephemeral: bool | None = None` 추가.
- `rooms/router.py::update_room`: ephemeral 변경도 broadcast 트리거에 포함.
- `ws/handler.py::welcome` 빌드 로직에서 Room.ephemeral / Agent.memory_md를 같은 세션에서 읽어 프레임에 실음.
- `agent/doorae_agent/client.py`: welcome 흡수부에 `self._memory_md` (agent 단일 스칼라) + `self._room_ephemeral: dict[str, bool]` 캐시. `room_settings_changed` 수신 시 ephemeral 캐시 갱신.

### Phase 4 — Agent runtime 공통 helper

- `packages/agent/doorae_agent/memory/compose.py` (신규) — `compose_memory_block(memory_md, ephemeral) -> str`. `<memory>` / `<memory-policy>` / `<ephemeral-session>` 블록을 한국어로 명시.
- `integrations/base.py::compose_memory_suffix(client, room_id) -> str` — 클라이언트에서 memory/ephemeral 상태를 꺼내 compose 결과를 반환. **둘 다 공백/기본값이면 빈 문자열**을 반환해 기존 테스트(system_prompt 정확 일치 assertion)의 회귀를 방지.
- 3개 어댑터 통합:
  - `claude_code.py::_build_options` — 기존 `system_prompt` 뒤에 suffix를 append (orchestrator roster suffix와 같은 시점).
  - `codex.py::on_message` — Codex thread는 history를 네이티브로 유지하므로 **첫 턴에만** suffix를 turn_content prefix로 주입 (`_memory_injected: set[str]`). 이후 턴은 이미 thread history에 남아있음.
  - `gemini_cli.py::_build_prompt` — 호출자가 `room_id`를 넘기도록 시그니처 확장 (`room_id: str | None = None` 기본값). 프롬프트 preamble 직후에 suffix 삽입.
- `tests/test_memory_compose.py` (신규, 7 cases): empty/whitespace/populated memory × ephemeral on/off 매트릭스 + trailing newline / 섹션 순서 보장.

### Phase 5 — Machine 레이어 (spawn/sync)

- `packages/machine/doorae_machine/protocol/frames.py`:
  - `SyncDesiredStateFrame`에 `memory_md: str | None = None`.
  - 신규 `AgentMemoryUpdateFrame(agent_id, memory_md)` — machine→cluster 방향.
  - `MachineFrame` Union에 추가.
- `spawner.py::SpawnManifest`에 `memory_md` 필드. `_materialize_agent_dir`에서:
  - `<agent_root>/memory/` (mode 0o700) 생성.
  - `notes.md` (mode 0o600)에 `msg.memory_md or ""`를 **항상 기록** — prune이 `workspace/` 외 모든 경로를 wipe하므로 re-spawn 시에도 일관되게 DB 스냅샷을 재주입. 파일은 런타임 truth이므로 다음 heartbeat가 덮어씀.
  - `get_agent_root(agent_id)` 접근자 신규 추가 (daemon sync-back이 참조).
- `daemon.py::_report_actual_state`의 끝에 `_flush_memory_updates()` 호출. 각 running agent의 `memory/notes.md`를 sha256 비교해서 바뀐 것만 `AgentMemoryUpdateFrame`으로 전송. 파일 부재는 silent skip (pre-#237 agent 호환).
- `daemon.py`의 SyncDesiredStateFrame → SpawnManifest 변환에서 `memory_md=getattr(manifest, "memory_md", None)` 전달.
- `cluster/scheduler/lifecycle.py::_build_sync_frame`에서 DB의 `agent.memory_md`를 frame에 포함.
- `cluster/ws/machine_handler.py`에 `agent_memory_update` 분기 신규 — `UPDATE agents SET memory_md=? WHERE id=?`.
- 테스트:
  - `test_materialize.py::TestMemoryMaterialize` (4 cases): 내용 있는 memory, 빈 memory, re-spawn 시 DB 스냅샷 재주입, AGENTS.md에 `memory/notes.md` 경로 언급 확인.
  - `test_protocol_frames.py::TestMemoryFrames237` (3 cases): SyncDesiredState parse, 기본값 None, AgentMemoryUpdate 직렬화.
  - `test_daemon.py::TestMemorySyncBack237` (4 cases): 첫 관측 송신, 동일 body 재송신 억제, 변경 시 재송신, 파일 부재 silent.

### Phase 6 — AGENTS.md 컨벤션

- `spawner.py::_compose_agents_md` 내 `## Memory` 섹션을 완전히 재작성. 기존 문구는 `workspace/MEMORY.md` overwrite 모델을 가리켰지만 이번 작업의 `memory/notes.md` append 모델과 충돌하므로 교체. 한국어로:
  - 경로 `memory/notes.md` 명시.
  - "append, 절대 overwrite 아님", "커지면 prune".
  - `<ephemeral-session/>` 토큰 감지 시 쓰지 말 것.
  - "머신이 주기적으로 DB에 sync한다"는 신뢰 모델 설명.

### Phase 7 — Frontend 사이드바 트리

- `hooks/useRooms.ts`:
  - `Room` 타입에 `ephemeral?: boolean`.
  - 신규 `createAgentDM(agentId, name?)` — `POST /api/v1/agents/{id}/dms` 호출 + `fetchAgentDMs()`.
  - 신규 `setRoomEphemeral(roomId, bool)` — optimistic update (agentDMs + project rooms 양쪽) + 서버 실패 시 롤백.
  - `doorae:rooms:settings-changed` 윈도우 이벤트 listener 추가 → 다른 탭/세션에서 온 ephemeral 변화 반영.
- `hooks/useWebSocket.ts`: `room_settings_changed` 프레임 분기 추가 → 위 윈도우 이벤트 발행.
- `components/Sidebar.tsx::AgentDMListAdmin` 대폭 리팩터:
  - `loadExpandedAgents/saveExpandedAgents` 유틸 (localStorage key: `doorae_expanded_agents_v1_{userId}`) — #234 topology user-scope 패턴과 동일 (try/catch 쉴드).
  - `grouped = { byAgent: Map<agentId, {agent, dms}>, orphans: Room[] }` — DM을 agent별로 묶고 이름순 정렬.
  - Single-DM 에이전트는 **inline** (chevron 없음, 기존 flat UX 유지). Multi-DM 에이전트는 collapse chevron + DM 카운트 배지 + 하위 DM 목록 (ml-5 indent + 왼쪽 whisper 보더).
  - 행 hover 시 `+ 새 대화` 버튼 노출 (기존 kebab 메뉴 옆). 클릭 → `createAgentDM` + 자동 expand + 새 방으로 navigate.
  - 각 DM에 `임시` 배지 (ephemeral=true일 때).
- DESIGN.md 준수: border `bg-black/5` whisper-weight, accent는 active ephemeral 토글에만 Notion Blue `#0075de`.

### Phase 8 — 방 헤더 Ephemeral 토글

- `components/RoomHeader.tsx`:
  - 새 props `ephemeral?: boolean`, `onToggleEphemeral?: (next: boolean) => void`.
  - DM + `onToggleEphemeral` 제공 시에만 렌더. 아이콘: 활성 `EyeOff` + Notion Blue 배경, 비활성 `Eye` + 그림자 없는 outlined ghost-button.
  - `aria-pressed`, `data-testid="room-header-ephemeral-toggle"` 추가.
- `pages/ChatPage.tsx`: RoomHeader 호출부에 `ephemeral={currentRoom.ephemeral ?? false}`, `onToggleEphemeral={isDm ? async next => setRoomEphemeral(...) : undefined}` 추가. `useRooms` 구조분해에 `setRoomEphemeral` 추가.

### Phase 9 — 기타

- 워크로그(이 문서).
- 기존 에이전트 / 기존 DM 대응: 마이그레이션 030 후 `memory_md=NULL` → spawner가 빈 파일 seed → 첫 쓰기부터 정상 동작. 기존 DM은 그대로 "첫 DM"으로 사이드바에 표시됨 (`representative_agent_id`가 NULL인 구 DM은 `findAgentForDM`의 name fallback으로 여전히 매칭).

## Decisions

전체 설계 결정은 `.tmp/plan-237-multi-session-dm-file-memory.md` §3.2에 상세히 기록되어 있음. 요점만 남김.

**핵심 결정 1 — runtime 압축 vs 방 분할 + 파일 기억**: 후자(C) 채택. 이유는 3개 엔진 모두 네이티브 파일 접근을 제공하므로 "FS 컨벤션 + system_prompt 주입"이 모든 엔진의 **최소 공통분모**가 됨. 엔진별 커스텀 MCP 도구 등록은 Codex/Gemini에서 동적 주입 경로가 불명해 실현성이 낮았고, rolling summary는 요약 품질 리스크가 큼.

**핵심 결정 2 — Ephemeral 강제 모델**: trust 모델(A) 채택. 파일 쓰기를 hook으로 금지하는 B안은 `#181` 사례처럼 에이전트가 "왜 못 쓰지?" 혼란을 일으키고, 스테이징→커밋 모델(C)은 3개 엔진의 쓰기 경로를 일관 후킹하기 어려움. Ephemeral은 **보안 경계가 아니라 사용자 의도 표명**이라고 정의하고 시스템 프롬프트 지시문으로 충분하다고 판단.

**핵심 결정 3 — DB↔파일 sync 방향**: B안(DB는 스냅샷, 파일이 런타임 truth) 채택. `agents_md`는 사람 편집이라 단방향이면 충분하지만 `memory_md`는 **에이전트가 쓰는** 문서이므로 역방향이 필수. 충돌 정책은 "파일 우선". 최대 유실 상한은 report 주기(현재 30s).

**결정 4 — 사이드바 UI 형태**: Adaptive(B안) — DM=1이면 inline, ≥2면 chevron+배지. 대부분의 초기 유저는 에이전트 1-3개 × DM 1-2개이므로 "지금도 편하고 커져도 버틴다"가 목표. DESIGN.md whisper-weight border 철학과 충돌하지 않는 수준의 시각적 위계만 추가.

**결정 5 — Codex 주입 지점**: Codex thread는 history를 네이티브로 유지하므로 `system_prompt`에 해당하는 SDK 파라미터가 없음. 첫 턴의 `turn_content` prefix로 한 번만 주입하고 `_memory_injected`로 재주입을 억제. 이 방식은 기술적 한계와 실용성의 타협이며, Codex가 향후 `instructions` 파라미터를 지원하면 Claude Code 패턴으로 통일할 예정.

**가정 / 재평가 트리거**:

- 에이전트가 ephemeral 지시를 준수한다는 가정. 준수율 측정 메커니즘은 v2에서 machine 레이어 쓰기 감사로 추가 예정. 현 시점에는 system prompt + AGENTS.md의 **이중 명시**로 강화.
- `memory/notes.md` 크기 폭주 리스크. AGENTS.md에 "너무 길면 prune/요약" 지시를 포함시켰지만 v1에는 자동 롤오버 없음. 운영 중 파일 크기 임계 모니터링이 필요하면 별도 tick에서 추가할 예정.

## Result

두 축(대화 경계 + 파일 기억) + 사용자 의도 시그널(ephemeral)이 모두 크로스엔진으로 동작함.

- **cluster tests**: 699 passed, 1 deselected (기존 699 → 0 regression; 마이그레이션 test의 head revision assertion만 교정).
- **machine tests**: 294 passed (기존 283 → +11 신규: memory materialize 4, protocol frames 3, daemon sync-back 4).
- **agent tests**: 273 passed (기존 265 → +8 신규: `test_memory_compose.py` 7 + minor). 기존부터 있던 `test_openai.py::test_integrate_registers_handler` (OPENAI_API_KEY 누락) 실패는 main에서도 동일 재현 → 본 PR과 무관, deselect.
- **frontend build + tests**: `npm run build` ✅, `npm test` 291 passed (기존 대비 0 regression; 새 sidebar 렌더는 기존 assertion과 호환).
- **ruff**: 수정 파일 내 신규 경고 없음. agent gemini_cli.py의 pre-existing `E401 import os, signal` 경고는 이번 작업 범위 밖.

**Trust 모델 노트**: ephemeral은 에이전트의 system_prompt 지시에 의존하므로 보안 경계가 아님. 사용자가 "이 대화는 기억되지 않기를 원함"이라고 명시한 신호를 에이전트에게 전달하는 UX 장치로 이해해야 함. 준수율은 v2 감사 로그로 계측 예정.

**Deferred**:

- Memory file 크기 자동 롤오버 — 현재는 agent self-pruning.
- Multi-machine 이주 시 memory file 이관 — 현재는 DB 스냅샷 기반으로 동작하지만 아직 실제 이관 시나리오 미테스트.
- Codex `instructions` SDK 파라미터 지원 시 Claude Code 스타일로 통일.
