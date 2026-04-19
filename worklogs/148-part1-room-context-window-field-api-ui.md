# feat(rooms): persist per-room context_window_enabled (#148 Part 1)

- Commit: `660eeee`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #148 (issue, 4-part follow-up to #74)

## Situation

PR #141 (Stage B, `cc32905`)로 ambient context window가 agent-side로 들어왔지만, on/off 단일 스위치가 machine 프로세스 env `DOORAE_CONTEXT_WINDOW_ENABLED`에 매여 있어 실운영에서 두 축이 비게 됐다: (1) machine 단위 일괄이라 같은 머신의 `#general`과 `#sensitive-dm`이 동일 설정을 공유 — 룸별 차등 불가, (2) 토글하려면 agent 재기동 필요. 운영자가 "Gemini 토큰 비싸니 이 에이전트만 제외"·"이 룸에서만 ambient 공유"를 못 해 Stage B의 가치가 반감. 이슈 #148은 이 문제를 룸·에이전트 DB 필드로 승격하는 4-PR 이행을 설계했고, Part 1은 그 중 **룸 측 스토리지와 UI** 최소 단위.

## Task

- `rooms.context_window_enabled BOOLEAN NOT NULL DEFAULT FALSE` DB 컬럼 추가 (migration 022)
- Room ORM 모델 + Pydantic `RoomOut`/`RoomDetailOut`에 필드 노출
- `PATCH /api/v1/rooms/{id}` body에 `context_window_enabled: bool | None` 허용 (partial update 의미 보존)
- `RoomEditDialog`에 "대화 맥락 공유" 토글 섹션 추가, GET 시 현재값 로드하고 Save 시 PATCH에 포함
- 백엔드 pytest + 프론트엔드 vitest 커버리지
- **동작 변화는 Part 3에서** — 이 PR은 필드가 저장/표시되지만 broadcast 흐름에는 영향 없어야 함

## Action

### 스토리지
- `packages/cluster/doorae/db/migrations/versions/022_room_context_window.py` — batch_alter_table + `server_default=sa.text("0")` (SQLite 호환). 017 avatar 마이그레이션 패턴 준수.
- `packages/cluster/doorae/db/models.py::Room` — `context_window_enabled: Mapped[bool]` with `server_default=sa_text("0")`, default=False.

### API
- `rooms/router.py::RoomUpdate` — `context_window_enabled: bool | None = None` (None=touch하지 않음, 기존 name/description 패턴 동일).
- `rooms/router.py::update_room` — None이 아닐 때만 반영.
- `rooms/router.py::RoomOut` — 필드 기본값 False로 추가, 리스트/GET 양쪽 응답 구성부에서 `r.context_window_enabled` 복사.

### Frontend
- `packages/cluster/frontend/src/components/RoomEditDialog.tsx` — `contextWindowEnabled` state, load 시 `data.context_window_enabled` 초기값, Save PATCH body에 포함. 섹션 UI는 체크박스 + 제목 "대화 맥락 공유" + 설명 "다른 에이전트의 응답·잡담도 …(토큰 비용 증가 가능)". `data-testid="room-edit-context-window-toggle"` 부착.

### 테스트
- `tests/test_rooms.py::TestRoomContextWindow` — 기본값 False, PATCH 토글 후 GET 재조회로 persist 확인, partial update (name만 변경)가 기존 토글을 덮지 않는지.
- `tests/test_migrations.py` — 기존 head `"021"` → `"022"`로 bump (5곳, round-trip 보증).
- `frontend/src/components/RoomEditDialog.test.tsx` — 초기 load 시 토글 반영, 토글 후 Save 시 PATCH body에 `context_window_enabled: true` 포함 (vitest + jsdom + fetch stub).

## Decisions

이슈 #148 계획 문서 `.tmp/plan-148-context-window-settings-ui.md`의 결정을 그대로 승계하고, 구현 세부에서만 추가 판단:

**컬럼 타입 — `BOOLEAN NOT NULL DEFAULT FALSE` 선택 근거**
- A. `NULL` 허용 후 "미설정 = 기존 env 경로" 하이브리드 — 이행 중 브랜칭 증가, 반영 타이밍 헷갈림
- B. NOT NULL + 기본 FALSE → **선택**. 존재하는 모든 룸이 기존 동작(ambient off)을 그대로 유지하고, 운영자가 명시적으로 on을 선택해야만 효과가 나타남. env 경로는 Part 3/4에서 독립적으로 deprecate. 마이그레이션 시 SQLite가 NOT NULL + 기존 row 조합을 거부하지 않게 `server_default=sa.text("0")`를 반드시 붙임 (017 avatar와 동일 패턴).

결정적 근거: Stage B env의 default도 FALSE였으므로 행동 변화 0. DB 기본값을 FALSE로 두면 기존 운영자가 Part 1 배포 후 "뭔가 바뀌었네?" 하고 놀랄 일이 없음.

**PATCH body의 `None` 의미 — "touch하지 않음"**
- A. `context_window_enabled: bool = False`가 기본값 — name만 변경하는 PATCH가 실수로 플래그를 False로 리셋
- B. `bool | None = None`, None이면 skip → **선택**. 기존 `name`, `description` 필드가 이미 같은 semantics이라 일관성.

결정적 근거: 프론트는 항상 두 필드를 같이 보내는 현 구현에서는 차이 없어 보이지만, 미래에 다른 UI 경로(예: room header inline toggle)가 이 필드만 PATCH할 때 방어막이 된다. 테스트 `test_patch_name_leaves_context_window_unchanged`로 semantics를 못박음.

**UI 컨트롤 — 네이티브 `<input type="checkbox">` 선택**
- A. shadcn `Switch` 컴포넌트 — 이 repo의 `components/ui/`에 아직 없음. 추가하려면 Radix `@radix-ui/react-switch` 의존성 신설
- B. 네이티브 checkbox → **선택**. `CreateSubRoomDialog`가 이미 같은 방식(`components/CreateSubRoomDialog.tsx:239`)을 쓰고, dependency 증가 없음. 디자인 시스템(DESIGN.md §4)은 checkbox도 허용 범위.

결정적 근거: Part 1은 스토리지 검증이 주이고 UI 정교함은 나중 리팩터 여지를 남겨둠. YAGNI.

**테스트 스코프 — vitest에서 `fetch` 전역 stub**
- A. `apiFetch`를 `vi.mock` — 모듈 모킹 추가 오버헤드
- B. 전역 `fetch`를 `installFetch` 헬퍼로 교체 → **선택**. `apiFetch` 구현이 단순 fetch 래퍼(`lib/api.ts`, 11줄)이므로 같은 계층에서 stub하는 게 실제 네트워크 호출 형태와 동등.

결정적 근거: mock 서피스 최소화. 이 파일 외에는 전역 fetch stub 패턴이 없지만, 이 컴포넌트는 apiFetch 외에 의존성이 거의 없어 도입 비용도 작다.

가정/이행 관찰점:
- Part 2(에이전트 opt-out), Part 3(서버 broadcast 부착)가 merge되기 전까지 이 토글은 "저장은 되지만 동작은 바뀌지 않음" 상태. 운영자가 토글을 on으로 세팅해도 env가 off면 ambient 메시지가 오지 않음 — Part 3 merge 시점에 자동 활성화될 것. UI 툴팁/릴리즈 노트로 이 "지연 적용"을 Part 3에서 안내 예정.

## Result

- `rooms.context_window_enabled`가 DB·API·UI 3계층에 모두 존재
- cluster pytest 581/581 통과 (migration head bump 포함, 기존 회귀 없음)
- frontend vitest: 신규 2 케이스 포함 전 슈트 통과, `npm run build` (tsc + vite) 경고만 (chunk size)
- 모노레포 `make test`는 기존 conftest 경로 충돌(#148 범위 밖)로 패키지별 개별 실행; 개별 실행 시 cluster/machine 모두 clean, agent는 main과 동일한 OpenAI env 누락 실패 1건으로 Part 1 변경과 무관
- 동작 변화 없음 — Stage B env 경로가 여전히 유일한 ambient 경로. Part 2/3 머지 시 UI 토글이 실제 broadcast 동작에 연결된다.
