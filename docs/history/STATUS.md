---
title: Implementation Status
updated: 2026-04-15
auto_generated: true
---

# Implementation Status

## Summary

| Status | Count | Description |
|--------|-------|-------------|
| done | 126 | 동작하는 코드 + 테스트 |
| stub | 2 | 코드 있으나 실제 동작 안 함 |
| reverted | 1 | feat/web-ui에서 작업 후 롤백 |
| planned | 2 | 설계만 있고 코드 없음 (Phase 2, 4) |

**Tests**: 610개 (cluster 316 + SDK/agent 87 + machine 207). `test_e2e_real_conversation.py` 는 실제 codex 서브프로세스를 띄우는 dev-only `pytest.mark.slow` 테스트라 정규 실행에서 `addopts = "-m 'not slow'"` 로 자동 제외.
**Versions**: doorae-server 0.2.0, doorae-sdk 0.1.0, doorae-machine 0.1.0

## Recent sessions

### 2026-04-15 — 익명 게스트 참여 RFC + 룸 헤더 정리

브랜치: `docs/guest-rfc`, `feat/guest-*` 시리즈, `feat/participant-list-visibility`, `fix/participant-toggle-hover`, `feat/room-header-overflow-menu` (PR #23–#34, 13 PR)

**Anonymous guest participation (RFC #22 / PR A–H, #23–#31)**:
- `User.is_anonymous` + `display_name` + `email/password_hash` nullable, partial unique index `ux_users_email_not_null` (마이그레이션 013)
- `RoomInviteLink` 테이블 + admin API (`/rooms/{id}/invites` POST/GET + `/invites/{id}` DELETE), argon2 해시 + `inv_` 12자 lookup hint, 10/min/user rate limit + 룸당 활성 상한 20 (마이그레이션 014)
- `Identity.kind="guest"`, `GuestClaims` JWT (`room_id` 바인딩), `POST /auth/guest` 토큰 발급, `forbid_guest` dependency + `require_room_member`에 게스트 분기
- WS `SendFrame` 게스트 분기: stricter cooldown (cap=3, refill=0.5/s), 멘션 allowlist `{user, legacy}` (`#room` 스트립), `GuestRoomAggregateLimiter` (룸당 20/min), representative 자동 합류 defense-in-depth
- 읽기 경로 게스트 격리: `list_rooms`에 `Room.id == claims.room_id` 추가 필터, `get_room`/`sub-rooms`에 403-first, `/projects` + `/saved` + `/machines` 전면 `forbid_guest`, `messages` 라우터 `require_room_member` 호출
- 프론트 호스트: `RoomInviteDialog` (생성/복사 1회/revoke), 버튼 게이팅 `admin OR room admin/owner`
- 프론트 게스트: `/invite/:token` 닉네임 폼 + `/g/:roomId` 단일 룸 셸, 기존 `doorae_token`을 `doorae_token_prelogin`에 백업 후 덮어써 로그아웃 시 복원, 401/403 시 자동 이탈
- 운영: `doorae.guest.anonymize` CLI + async util (30일 경과 revoked/expired 게스트의 display_name을 `(former guest)` 센티넬로 덮어씀, idempotent), Prometheus 메트릭 4종 (`guest_active`, `invites_created_total`, `invites_used_total`, `guest_rate_limited_total{scope}`)
- 디자인 문서 `docs/design/11-anonymous-guests.md` 전체 집필 (정책/토큰 포맷/권한 매트릭스/rate limit/라이프사이클/구현 현황 맵)
- 각 PR마다 전문가 에이전트 1회 코드 리뷰 후 머지 — 주요 critical 이슈 10+건 반영 (SQLite partial index 마이그레이션 패턴, authz 403-before-404 순서, TOCTOU 문서화, 누수 guard 플래그, 포커스 트랩 대신 role=group 등)

**WS 프로토콜 문서 싱크 (PR #21, 선행 작업)**:
- `docs/design/01-architecture.md §1.5` C2S/S2C 프레임 표 재작성 — `protocol.py` 실제 정의와 1:1 일치. `send_message`/`leave_room`/`create_sub_room`/`message_ack`/`participant_joined`/`_left`/`rate_limited`/`resync_required` 삭제, `create_room`/`join_room`(S2C)/`room_membership_changed` 추가

**Participant list popover (PR #32)**:
- `ParticipantOut`에 `is_anonymous: bool` (default False, 외부 호환 유지)
- `ParticipantListPopover` 컴포넌트 — agent → user → guest 그룹 정렬, 배지 (guest면 role 배지 억제), outside-click/Escape 닫기, `role="group"` (포커스 트랩 없음)
- `RoomHeader`의 참여자 수 아이콘을 클릭 가능한 토글 버튼으로 전환 (모바일 포함). `ChatPage`와 `GuestRoomPage` 모두 같은 컴포넌트 사용 → 게스트도 로스터 확인 가능
- 게스트 프라이버시 경계: display_name / kind / role / is_anonymous 만 노출, email/user_id 비노출 — 디자인 문서 §11.5에 명문화

**Ghost-button hover 규약 준수 (PR #33)**:
- 참여자 토글이 `hover:bg-[var(--color-background-muted)]`로 렌더링되면 해당 변수가 정의 안 돼 하이라이트 누락 → `hover:bg-black/5 cursor-pointer`로 교정 (전역 ghost 버튼 컨벤션, STATUS.md:58 참조)

**룸 헤더 overflow 메뉴 (PR #34)**:
- 관리자 룸에서 7개 이상의 인라인 버튼이 참여자 카운트까지 밀어내던 문제 해결
- `RoomSettingsMenu` 신규: `…` 트리거 하나로 Sub-room / Edit / Invites / Manage agents / Stop All 을 묶음. Stop All은 separator + `text-red-600`로 파괴적 섹션 분리
- 상태 표시(참여자 카운트, 대표 에이전트 select, 연결 배지)는 그대로 헤더에 유지 — "한눈에 읽는 룸 상태"는 클릭 없이
- `pointerdown` outside-click (iOS Safari 지원), `role="group"` + `aria-haspopup="dialog"` (실제 구현하지 않는 메뉴 역할 semantic 피함), useEffect 전에 early return 두지 않음 (hooks 순서 안정)
- UI 컨벤션 추가: **룸 헤더의 mutation 액션은 overflow 메뉴로, 상태 read-out은 인라인 유지** — 향후 버튼 신설 시 회귀 방지 기준

**결과**: cluster 316(+81 from 235), SDK 87(+3), machine 207(+90). 총 610 tests.

### 2026-04-14 — DX 개선 + 에이전트 DM + 활동 히스토리 + 버그 수정

브랜치: 다수 (PR #22–#33, 12 커밋)

**Makefile 개발 프로세스** (PR #22):
- `make dev`: 백엔드(8001) + 프론트엔드(5173) 동시 기동, `DOORAE_DEV=1` 자동 설정
- `make backend`, `make frontend`, `make test`, `make build`
- `DEV_PORT` 변수로 백엔드 + Vite 프록시 포트 자동 연동
- Vite 프록시 타겟 8000→8001 수정, `host: true` 추가 (LAN 접속)

**채팅 레이아웃** (PR #21):
- `max-w-3xl mx-auto` 중앙 정렬 — 넓은 화면에서 읽기 편한 폭
- 에이전트 메시지는 컨테이너 전체 너비, 사용자 메시지는 70% 제한

**에이전트 자동 DM 룸** (PR #24, #25, #26):
- 에이전트 생성 시 자동 DM 룸 (`is_dm=true`) 생성 → "no rooms assigned" 에러 해소
- `GET /rooms` API: `is_dm` 쿼리 필터 추가, `project_id` Optional 변경
- 사이드바 "Agents" 섹션: DM 채널 별도 표시 (접기/펼치기)
- ChatPage: DM 룸 클릭 시 채팅 페이지 정상 렌더링
- DM 페이지 mutation 후 `fetchAgentDMs()` 호출 (상태 동기화)

**활동 히스토리** (PR #27, #28, #29):
- `MachineActivityLog` 별도 테이블 + migration 011 — online/offline/drain 이벤트
- `GET /machines/{id}/activity` API
- 머신 상세 History 섹션 (이벤트 타임라인)
- 에이전트 관리 페이지 History 버튼 → activity 다이얼로그
- 에이전트 activity: heartbeat 노이즈 제거 (상태 변경만 `state_changed`로 기록)
- 에이전트 메시지 이벤트: `message_received`, `processing_started`, `response_sent`

**에이전트 정지 상태 동기화** (PR #30):
- heartbeat 보고에서 빠진 에이전트 중 `desired=stopped` → `actual=stopped` 전환

**머신 페이지 에이전트 UX** (PR #31, #32):
- 정지 시 `placed_on_machine_id` 유지 → 머신 페이지에서 정지 에이전트도 표시
- 정지 에이전트 opacity-50, Play/Square 시작/정지 아이콘
- ghost 버튼 hover: `hover:bg-black/5 cursor-pointer` 전역 적용
- capacity 표시: 활성(running/starting/pending) 에이전트만 카운트

**SDK 의존성** (PR #33):
- `claude-agent-sdk`, `openai`, `anthropic`을 기본 의존성으로 이동 (optional → default)

**결과**: server 235(+3), SDK 84, machine 117. 총 436 tests.

### 2026-04-13 — 룸 대표 에이전트 (#룸 멘션 → 의견 취합)

브랜치: `feat/room-representative-agent` (4 커밋)

**개요**: 각 룸에 대표 에이전트를 지정하여, 사용자가 `#룸` 멘션 시 대표 에이전트가 해당 룸의 다른 에이전트들에게 실제 질문을 던지고, 전원 응답을 취합해 요청 룸에 종합 답변을 전달.

**서버**:
- DB: `rooms.representative_agent_id` FK (SET NULL) + migration 010
- API: `PUT /rooms/{room_id}/representative` (admin only) — 대표 지정/해제. 에이전트가 룸 참여자인지 검증
- `RoomOut` / `RoomDetailOut` 스키마에 `representative_agent_id` 포함
- WS handler: `#룸` 멘션 감지 → 대표 에이전트 조회 → 미참여 시 영구 참여 → `room_query` metadata 부착 (`{target_room_id, source_room_id}`)
- offline 대표 에이전트 → "대표 에이전트가 오프라인입니다" 에러 프레임

**SDK**:
- `should_respond`: `room_query` metadata + `[ROOM_QUERY]` 접두사 조건 추가
- `client.py`: `get_room_participants()` 헬퍼 + `[ROOM_QUERY]` 턴 카운터 리셋
- `room_query.py` (신규): `parse_room_query()`, `execute_room_query()`, `_register_multi_reply_callback()` — 복수 에이전트 응답 수집 + 전원 응답/5분 타임아웃 후 취합 → source room에 결과 전달
- 어댑터 3종 (codex/gemini/claude) `_handle`에 room_query 분기 추가

**프론트엔드**:
- `RoomHeader.tsx`: admin 전용 대표 에이전트 선택 드롭다운
- `ChatPage.tsx`: `agentParticipants` memo + `handleSetRepresentative` 콜백
- `useRooms.ts`: Room 인터페이스에 `representative_agent_id` 추가

**채팅 레이아웃 개선**:
- `ChatArea.tsx`, `MessageInput.tsx`, `TypingIndicator.tsx`: `max-w-3xl mx-auto` 적용 — 넓은 화면에서 메시지 영역을 768px 중앙 정렬
- `MessageBubble.tsx`: 에이전트 메시지는 컨테이너 전체 너비, 사용자 메시지는 기존 70% 제한 유지

**설계 문서**: `docs/plans/2026-04-13-room-representative-agent-design.md`

**결과**: server 232(+6), SDK 84(+8), machine 117. 총 433 tests.

### 2026-04-13 — 멘션 시스템 (@user, #room)

브랜치: `feat/mention-system` (11 커밋)

**설계**: ID 기반 토큰 포맷 (`<@user:abc123>`, `<#room:xyz789>`) 으로 메시지 `content` 에 인라인 저장 + `extra_metadata.mentions` 배열로 서버 측 파싱 결과 병행 보관. 프론트엔드에서는 사용자에게 표시명 (`@홍길동`) 으로 보여주고 전송 시 ID 토큰으로 변환. 레거시 `@Name` 포맷도 하위 호환으로 서버 파싱 지원.

**서버 (orchestration/rules.py + ws/handler.py)**:
- `parse_mentions()` — ID 기반 (`<@user:id>`, `<#room:id>`) + 레거시 (`@Name`) 이중 파싱. ID 기반이 있으면 레거시 무시
- WS handler 가 `SendFrame` 수신 시 `parse_mentions()` 호출 → `extra_metadata.mentions` 에 머지

**프론트엔드**:
- `lib/mentions.ts` — `parseMentionTokens()`, `insertMentionToken()`, `extractMentionsMetadata()` 유틸리티
- `MentionPopover.tsx` (신규) — 자동완성 드롭다운. 키보드(↑↓/Enter/Tab/Esc) + 마우스 지원. 유저/에이전트(🤖)/룸(#) 시각 구분
- `MessageInput.tsx` — `@` 트리거 → 참여자 목록, `#` 트리거 → 룸 목록. 입력 중 실시간 필터. textarea 에는 표시명 노출, 전송 시 ID 토큰 변환 (`trackedMentions` 맵)
- `MarkdownContent.tsx` — 메시지 본문의 `<@user:id>` / `<#room:id>` 토큰을 렌더링. 유저 멘션 → Notion Blue 배경 `@Name` 스팬, 룸 멘션 → 클릭 가능 `#RoomName` 링크. 삭제된 유저/룸 폴백 ("알 수 없는 사용자")
- `MessageBubble.tsx` — `resolveUser()` / `resolveRoom()` 콜백을 `MarkdownContent` 에 전달. 참여자 맵 + 룸 컨텍스트에서 ID→표시명 해석
- `ChatPage.tsx` — `mentionUsers` / `mentionRooms` 메모이즈 상태를 `MessageInput` 에 전달
- `useWebSocket.ts` — `send()` 에 optional `metadata` 파라미터 추가

**테스트**: `test_mention_parsing.py` 6 tests (ID user/room, mixed, no-mentions, legacy, mixed-drops-legacy) + 기존 `test_orchestration.py` 멘션 관련 테스트 갱신.

**설계 문서**: `docs/plans/2026-04-13-mention-system-design.md` (설계), `docs/plans/2026-04-13-mention-system-plan.md` (구현 계획)

**결과**: server 226(+12), SDK 76, machine 117. 총 419 tests. 12 파일 변경, +1400/-31 줄.

### 2026-04-13 — UI 개선 + 활성 엔진 필터 + 경쟁 서비스 벤치마크

브랜치: `main` (7 커밋)

**UI 개선**:
- `ChatArea.tsx`: typing indicator 를 bounce dots 에서 **braille spinner** 로 교체 (더 작은 공간에 에이전트 이름 + 스피너)
- `ChatPage.tsx`: `100vh` → `100dvh` 로 변경 — 모바일에서 주소창 높이가 레이아웃에 포함되던 문제 수정
- `MessageBubble.tsx` + `MarkdownContent.tsx` 신규: 채팅 메시지 **마크다운 프리뷰** 지원 (코드블록, 인라인코드, 링크, 리스트)

**활성 엔진 필터 (PR #17)**:
- `GET /api/v1/agents/engines/available` 신규 엔드포인트: 온라인 머신이 지원하는 엔진만 반환 (`MachineEngine JOIN Machine WHERE status='online'`)
- `AdminAgents.tsx`: 에이전트 생성 다이얼로그의 엔진 드롭다운이 활성 엔진만 표시. 다이얼로그 열 때마다 refresh.
- `useAgents.ts`: `fetchAvailableEngines()` 훅 추가

**자가대화 수정 + sub-room delegation 머지**: `fix/agent-self-conversation-and-subroom` 브랜치가 main 에 머지 (이전 세션에서 작업, 아래 2026-04-12 섹션 참조)

**경쟁 서비스 벤치마크**: 경쟁 서비스 전체 기능 조사. 상세 비교 보고서 `.tmp/benchmark-competitor.md`. 주요 발견:
- 경쟁 강점: 채팅-태스크 통합, 스레드, 검색, Activity 로그, Push 알림, 에이전트 MEMORY.md
- Doorae 강점: 6종 엔진 다양성 (API+CLI), sub-room delegation, 셀프호스트, AGENTS.md/SKILL.md, should_respond 게이트
- Doorae 에 적용 가능한 기능 우선순위 정리 완료 (reasoning effort, 태스크, 검색, Activity 로그 등)

**결과**: server 214, SDK 76(+3), machine 117. 총 407 tests.

### 2026-04-12 — 에이전트 자가대화 수정 + sub-room delegation + 턴 제어

브랜치: `fix/agent-self-conversation-and-subroom` (10 커밋, main 머지 전)

**Root cause 분석 (DB/로그 포렌식 + Playwright 실측)**:
1. 서버 재시작 시 heartbeat reconcile 이 에이전트를 재spawn 하면서 **이전 프로세스를 kill 하지 않아 중복 프로세스** 생성 → 각자의 nonce set 이 독립적이라 상대방 응답을 새 메시지로 처리 → 자가대화 핑퐁
2. sub-room 생성 시 DB 에 Participant row 만 INSERT, **실행 중 에이전트 subprocess 에 알림 없음** → 서브룸 메시지 0건
3. `_restart_on_same_machine` spawn frame 에 rooms/token/files 누락 → crash loop

**수정 (P0–P2)**:
- `spawner.py`: spawn 시 기존 프로세스 kill 후 재생성 (중복 프로세스 근절)
- `ws/protocol.py` + `handler.py`: `WelcomeOut(participant_id, pending_rooms)` 프레임. 에이전트 (재)연결 시 자기 participant_id 학습 + 누락 서브룸 자동 join
- `client.py`: `_process_frame` 에 participant_id 기반 hard self-filter + welcome/join_room/message 통합 처리. WS 403 시 재시도 없이 포기
- `rooms/router.py`: sub-room 생성 시 parent room 에 `JoinRoomOut` broadcast → 에이전트 실시간 join
- `lifecycle.py`: `_restart_on_same_machine` 완전한 spawn frame. `on_agent_crashed` 에서 `desired_state=stopped` 존중 (Stop 버튼 동작 수정)
- `codex.py` + `gemini_cli.py`: `start_new_session=True` + `os.killpg()` 로 timeout 시 프로세스 그룹 전체 kill (orphan 방지)

**`/delegate` 명령 (v1)**: 메인룸 → 서브룸 작업 위임
- `@에이전트 /delegate 서브룸이름 작업내용` → 에이전트가 서브룸에 `[DELEGATED] 작업` 전달 → 서브룸 에이전트 응답 캡처 → 메인룸에 결과 보고
- `integrations/delegate.py` 신규: `parse_delegate`, `execute_delegate`, `_wait_for_reply`
- 어댑터 3종(codex/gemini/claude)에 delegate 분기 추가
- `GET /rooms/{id}/sub-rooms?name=` 엔드포인트 + `client.find_sub_room()` 헬퍼

**v2 자동 delegation**: AGENTS.md 에 `## Delegation` 섹션 자동 인라인
- `rooms.description` 컬럼 + migration 006 + UI (CreateSubRoomDialog description 필드 + RoomEditDialog 신규)
- `lifecycle.py`: spawn frame 에 sub_rooms `[{name, description}]` 포함
- `spawner._compose_agents_md()`: sub_rooms 정보로 Delegation 섹션 생성 (skills 인라인과 동일 패턴)

**턴 카운터 (무한 루프 방지)**: Claude Code 의 32-iteration hard limit 참고
- `client.py`: per-room 에이전트 연속 메시지 카운터. 사람 메시지 없이 6턴 초과 시 handler 스킵. `[DELEGATED]` 는 카운터 리셋 (새 작업 시작).
- 프롬프트 가드레일: Delegation 섹션에 "작업 완료 후 대화 이어가지 마라" 규칙

**ADR-003**: delegation orchestration strategy — LLM 판단 + 인프라 실행 원칙. Claude Code 유출 코드 분석 기반. 서브룸 이름+설명 기반 판단 (구성원 기반은 향후 옵션). v1→v4 진화 경로 문서화.

**Playwright 실측**: aab 직접 대화 ✓, `/delegate 최신정보수집룸 한국의 수도는?` → 서브룸 응답 "서울" → 메인룸 결과 보고 ✓, 서브룸 메시지 3개에서 정지 ✓

**`should_respond` 통합 게이트**: 구조적 불안정성 (mesh + reactive) 해소
- `base.py`: `should_respond(msg, client)` — 멘션/@mention + [DELEGATED] + 사람 메시지만 통과. 에이전트 간 미멘션 메시지는 무시.
- 어댑터 3종 `_handle` 최상단에 게이트 적용. 턴 카운터는 안전망으로 유지.
- 8 should_respond 단위 테스트.

**비동기 delegate**: `execute_delegate` 가 즉시 리턴. 콜백 기반으로 서브룸 응답 시 자동 메인룸 보고. 5분 safety timeout. 메인룸 메시지 루프 비차단.

**typing 인디케이터 수정**: `useWebSocket.ts` 의 setTimeout 스택킹 → debounce 방식 (clearTimeout + 5초 expire) 으로 교체. flickering 근절.

**결과**: server 214, SDK 73(+16), machine 117. 총 404 tests. 18 커밋. 33 파일 변경, +1387/-89 줄.

### 2026-04-12 — Sub-room UI + Agent Protocol 레퍼런스 조사 (PR #11)

**PR #11 — frontend sub-room 노출**: Phase 0 에서 `Room.parent_room_id` + `create_sub_room` service + `POST /rooms/{id}/sub-rooms` 가 이미 있었지만 프론트가 아무 데서도 쓰지 않아 curl 로만 생성 가능했던 상태. 이 PR 이 4개 컴포넌트 / 1개 Provider refactor 로 end-to-end 노출.

- **`Sidebar.tsx`**: `buildRoomTree()` 로 flat → tree 재구성, `RoomTreeBranch` 재귀 컴포넌트가 depth \* 12px (cap 4) 인덴트. 고아 room (parent 가 list 에 없음) 은 root 로 promote.
- **`RoomHeader.tsx`**: `parentBreadcrumb` prop + `← <parent>` 링크 + `Sub-room` (FolderPlus) 버튼.
- **`CreateSubRoomDialog.tsx` (신규)**: parent room 의 `/api/v1/rooms/{id}` 상세를 가져와 participant 체크박스 목록 생성. 현재 user 의 participant.id 는 `creator_participant_id` 로 자동 주입. 나머지 parent 멤버만 invitee 로 선택 가능.
- **`ChatPage.tsx`**: `parent_room_id` 체인을 32 hops cap 으로 walk 해서 breadcrumb chain 계산. 생성 후 `fetchRooms(project_id)` + `navigate(newRoom.id)`.

**Shared-state 버그와 수정**: 첫 Playwright round-trip 에서 sub-room 이 DB 에는 저장됐는데 sidebar 가 stale. 원인: `useRooms()` 가 plain hook 이라 `ChatPage`/`Sidebar` 가 각자 독립 state 를 가짐 → `ChatPage.fetchRooms()` 가 `Sidebar` state 를 안 건드림. 수정: `hooks/useRooms.ts` 를 `RoomsProvider` + `useContext` 로 리프트, `App.tsx` 에 마운트. 콜사이트는 동일 (`const {projects, rooms, ...} = useRooms()`). 두 번째 round-trip 에서 sidebar 즉시 업데이트 확인 (depth 1, paddingLeft 20px).

**실측 검증**: sub-room 생성 → sidebar 트리에 indent 된 자식 표시 → breadcrumb `← aab` → 클릭으로 parent 로 navigate → 전부 reload 없이 동작.

**Agent Protocol / Phase X 레퍼런스 조사**: 사용자 질문 "pi mono / OpenHands 는 sub-agent 호출과 스트리밍을 어떻게 처리?" 에 응답해 웹 검색으로 두 프레임워크의 최신 (2025-11 ~ 2026-04) 문서를 조사. 결과가 Phase X 설계의 근거가 되므로 [`plans/2026-04-11-per-agent-directory-skills.md#phase-x`](plans/2026-04-11-per-agent-directory-skills.md) 의 Phase X 섹션에 "pi-mono + OpenHands V1 레퍼런스" 서브섹션으로 추가.

- **pi-mono (badlogic/mariozechner)**: sub-agent 공식 미지원. 대신 `pi --print` 를 bash/tmux 에서 재귀 spawn. pi-ai 패키지가 **progressive tool args parsing** 제공 — LLM 이 tool arguments 스트리밍하는 동안 점진적 JSON 파싱으로 partial UI 업데이트 (diff 가 쓰이는 동안 그려지는 효과). 4개 wire protocol 추상화.
- **OpenHands V1 SDK (MLSys 2026)**: `DelegateTool` standard tool. `spawn` command 로 sub-agent ids 정의 → `delegate` command 로 task 매핑. Parallel threads + **blocking** + consolidated observation 패턴. Sub-agent 는 parent LLM config + workspace 상속, conversation context 는 독립. 스트리밍은 `token_callbacks` 로 4개 타입 분리 (reasoning / content / tool name / tool args).
- **doorae 관점**: 채널 기반 서브룸은 pi-mono 의 bash/tmux spawn 패턴과 가깝고, Phase X Agent Protocol 방향은 OpenHands DelegateTool + Deep Agents `AsyncSubAgent` 방향이 일치. doorae 의 WebSocket 채널 모델은 "sub-agent 응답 스트리밍" 이 공식 지원이라는 **다른 두 프레임워크 대비 고유 이점**.

**결과**: 총 390 tests (변경 없음). Frontend bundle 421KB gzip 122KB. main 동기화 완료.

### 2026-04-12 — Agent manifest editor (PR #8 backend + PR #9 admin UI)

Phase 0 이후로 DB 스키마만 깔려 있던 `agents.agents_md` + `agent_files` 테이블을 admin 이 웹 UI 에서 직접 편집할 수 있도록 엔드투엔드로 연결. 이 전에는 스킬이나 AGENTS.md 를 붙이려면 DB 를 직접 INSERT 해야 했음.

**Backend (PR #8)**: `doorae-server/doorae/api/v1/agents.py` 확장 — `POST /agents` 가 `agents_md` + `files` 를 받고, 신규 `PUT /agents/{id}` 가 `name` + `agents_md` 업데이트 (`agents_md_set: true` 플래그로 null 클리어 구분), 신규 `/agents/{id}/files` CRUD 3종 (list / upsert / delete) 추가. 모든 path 는 `validate_agent_file_path` 게이트. +15 tests = 213 server.

**Frontend (PR #9)**: `AdminAgents.tsx` 에 FileCog 버튼 추가, 신규 `AgentEditDialog` 컴포넌트 — AGENTS.md textarea + 파일 트리 (Skills / Codex / Claude Code / Gemini CLI / OpenHands 그룹핑) + 그룹 아래 파일 선택 시 오른쪽 editor 표시. Save 는 dirty 파일만 PUT, deleted 원본만 DELETE 로 batch. useAgents hook 에 `updateAgent`, `fetchAgentFiles`, `upsertAgentFile`, `deleteAgentFile` 4개 함수 추가.

**Playwright 실측 (PR #9 중)**: 다이얼로그 오픈 → AGENTS.md 수정 → Save → reload 시점에 textarea 가 stale prop 로 덮어쓰이는 버그 발견 → `loadInitial()` / `resyncAfterSave()` 분리 → Save 후에는 files 만 refetch 하고 agentsMd 는 로컬 authoritative 유지로 수정 → 재검증 (edit marker A + B 둘 다 UI + DB 에 유지). 새 파일 추가 + 삭제 왕복도 DB 레벨 확인.

**Option C (future UX)**: 스킬 라이브러리, MCP 서버 마켓, Monaco 구문 하이라이팅, clone from agent, live preview, history/undo, hot-reload 같은 비-개발자 admin 용 개선 아이디어 7가지를 [`docs/plans/2026-04-12-agent-editor-future-ux.md`](plans/2026-04-12-agent-editor-future-ux.md) 에 문서화. 실제 사용 evidence 가 쌓이면 착수.

**결과**: 총 390 tests (server 213 + SDK 60 + machine 117). dev box 에서 admin UI 로 실제 agent 의 AGENTS.md 와 skills 편집 가능 상태.

### 2026-04-12 — Gemini CLI 실측 + agent_root cwd / yolo approval 수정

브랜치: `feat/impl-doorae-chat-server`. gemini 0.37.1 바이너리를 dev box 에 설치한 뒤 Playwright 실측에서 두 가지 버그 발견 → 수정:

1. **`gemini_cli` 어댑터가 AGENTS.md / .gemini/settings.json 을 전혀 로딩하지 않았음.**
   근본 원인: gemini 의 `findProjectRoot` 가 `.git` 을 위로 탐색해서 프로젝트 루트를 결정하는데, per-agent 레이아웃엔 `.git` 이 없으니 cwd (= `agent_root/workspace/`) 를 프로젝트 루트로 고정. 그 결과 materializer 가 `agent_root/.gemini/settings.json` 에 써 둔 `context.fileName = "AGENTS.md"` 를 **gemini 가 절대 발견하지 못함**. 실측에서 `[SKILL: greeting]` 프리픽스와 역할이 그대로 빠져나가고 stock gemini 세션처럼 응답하던 이유.

   수정: 어댑터가 서브프로세스 cwd 를 `Path.cwd().parent` (= `agent_root`) 로 고정. gemini 가 agent_root 를 프로젝트 루트로 삼으면서 `.gemini/settings.json` + `AGENTS.md` 를 hierarchical memory 로 자동 로딩. workspace/ 는 subdirectory 로 유지돼 LLM 이 상대경로 스크래치 쓰기 가능.

2. **Time-check 스킬에서 gemini 가 무한 대기.**
   근본 원인: gemini 기본 `approval-mode` 가 `default` (= "prompt for approval") 라 non-interactive `-p` 모드에서 tool call (shell exec) 시 사람 승인을 기다리다 timeout. 120s 뒤 어댑터가 응답을 포기.

   수정: 어댑터가 `--approval-mode yolo` 를 명시. Codex / Claude Code 어댑터와 동일한 신뢰 모델 (unattended autonomous agent). YOLO 모드 배너는 gemini 가 stderr 로 출력하니 stdout JSON 파서에 영향 없음.

3. **`workspace/AGENTS.md` / `workspace/CLAUDE.md` 를 engine-aware hybrid 로 전환 (symlink vs real file copy).**
   근본 원인: gemini 의 `read_file` 툴이 파일 경로를 resolve 한 뒤 "allowed workspace directories" 바깥이면 거부하는데, `../AGENTS.md` 심볼릭은 resolve 후 `agent_root/AGENTS.md` 가 돼서 거부당함. Codex 는 심볼릭이 통했지만 gemini 는 엄격하게 체크.

   첫 수정 시도 (real file copy for all engines) 는 Codex stop-hook 에서 regression 으로 지적됨: symlink 접근은 **reads 는 통과, writes 는 sandbox 밖 경로로 resolve 돼 차단** 이라는 isolation 계약이었는데, real file copy 는 workspace 내부에 그대로 있어 agent 가 자기 자신의 AGENTS.md 를 overwrite 가능 → subsequent turn 에서 변조된 내용이 system prompt 로 로딩 → in-session prompt injection.

   최종 수정: `_materialize_agent_dir` 가 `msg.engine` 에 따라 분기.
   - **engine == "gemini-cli"**: real file copy, mode **0o400** (owner read-only). `open(..., O_WRONLY)` 가 EACCES 로 실패하는 speedbump. 에이전트가 `chmod u+w` 로 우회 가능하나 noisy 하고, 다음 spawn 의 materializer 가 canonical bytes 로 복원하므로 tamper 는 one session scope.
   - **engine in {codex, claude-code}**: 원래 symlink (`workspace/AGENTS.md -> ../AGENTS.md`). Codex 리뷰가 확정한 isolation 계약 유지 — reads resolve, writes resolve 후 sandbox 거부.

   신규 테스트: `test_creates_workspace_agents_md_symlink_for_codex`, `test_creates_workspace_claude_md_symlink_for_codex`, `test_creates_workspace_agents_md_real_copy_for_gemini`, `test_creates_workspace_claude_md_real_copy_for_gemini`. `test_workspace_agents_md_refreshed_even_if_tampered` 는 gemini 버전으로 개편 (chmod 후 overwrite → 다음 spawn 이 0o400 canonical 로 복원 검증). 추가: `test_workspace_agents_md_symlink_restored_after_tamper_codex` — codex 세션이 symlink 를 unlink 해서 regular file 로 바꿔치기 해도 다음 spawn 이 symlink 복원 확인.

4. **`test_e2e_materialize.py::test_server_frame_materializes_to_disk` pre-existing 실패 수정.**
   Phase 1.5 가 `AGENTS.md` 에 `## Available skills` 섹션을 자동 인라인하도록 바꾼 뒤 e2e 테스트가 업데이트되지 않아 `assert rendered == "# e2e agent\nBe helpful."` 가 실패 상태로 남아 있었음. `startswith` + 인라인 섹션 확인으로 교체.

**실측 (Playwright)**:
- `@테스트 에이전트 Gemini CLI agent_root cwd 적용 후 — 안녕!` → `[SKILL: greeting] 안녕하세요! 저는 Doorae 테스트 에이전트입니다. 코딩 어시스턴트입니다. 도와드릴 일이 있을까요?`
- `@테스트 에이전트 yolo 모드 적용 후 — 지금 몇 시야?` → `[SKILL: time-check] $ date '+%Y-%m-%d %H:%M:%S %Z' 현재 시각은 2026년 4월 12일 오전 2시 39분 17초(KST)입니다.`
- Hybrid materialize 적용 후: `@테스트 에이전트 hybrid materialize 적용 후 — 안녕! 그리고 지금 몇 시야?` → `[SKILL: greeting, time-check] 안녕하세요! 저는 Doorae 테스트 에이전트입니다. 코딩 어시스턴트 $ date '+%Y-%m-%d %H:%M:%S %Z' 현재 시각은 2026년 4월 12일 오전 3시 34분 00초(KST)입니다.` (0o400 real-file copy 에서도 gemini 가 AGENTS.md 를 문제없이 로딩)

**신규 회귀 테스트**:
- SDK: `TestCallGemini::test_cwd_is_agent_root_and_approval_mode_yolo` — `asyncio.create_subprocess_exec` 를 monkeypatch 해서 cwd 와 `--approval-mode yolo` 가 argv 에 들어가는지 고정. 이 두 불변이 깨지면 gemini 가 즉시 stock session 모드로 돌아가니 단위 테스트로 보호.
- Machine: 4 신규 materialize tests (codex symlink + gemini real-copy, AGENTS.md + CLAUDE.md 각각), `test_workspace_agents_md_refreshed_even_if_tampered_gemini` + `test_workspace_agents_md_symlink_restored_after_tamper_codex` — engine 별 isolation 계약 보호.

**결과**: SDK 60 tests (+1), 머신 117 tests (+3), 서버 198. dev box 의 3개 실측 엔진 (codex / claude-code / gemini-cli) 전부 동일한 AGENTS.md + skills/ 파일만 가지고 `[SKILL: greeting]` / `[SKILL: time-check]` 규칙을 준수. 한 개의 파일 기반 manifest → 3개 엔진에서 identical 행동이라는 Phase 0 의 원래 약속이 이제 gemini 에서도 증명됨. **Codex isolation 계약 (canonical AGENTS.md 는 sandbox 에서 write-unreachable) 이 symlink 폴백으로 유지됨**.

### 2026-04-12 — Phase 1 hardening + Phase 3 Claude Code + Phase 1.5 SKILL.md auto-inline

PR #3 (Phase 0 + 1 + 3), PR #4 (Phase 1.5). 둘 다 main 머지됨. 총 17 커밋 + 1 커밋 = **18 커밋이 main 에 반영**. 브랜치 `feat/per-agent-directory-skills`, `feat/codex-skill-inline` 삭제됨.

**Codex 어댑터 sandbox 하드닝 (Phase 1 후속, 4 커밋)**:
Playwright 실측 + 3건의 Codex stop-hook 피드백을 통해 다음 단계로 진화:
1. `3954c25` `-o` output file 경로를 cwd 로 옮김 — `tempfile.NamedTemporaryFile` 기본값(/tmp) 은 `workspace-write` 샌드박스 밖이라 차단당함.
2. `676b6e3` `-C <agent_root>` 명시적 전달 — `--skip-git-repo-check` 는 "git repo 없어도 에러 내지 말라" 일 뿐 상향 탐색은 막지 않음. 결과적으로 codex 가 조상 git repo (호스트 monorepo) 를 프로젝트 루트로 오인. 첫 실측에서 에이전트가 AGENTS.md 규칙을 완전히 무시한 원인.
3. `dc74cb7` `-C agent_root` → `-C workspace/` + `workspace/AGENTS.md → ../AGENTS.md` 심볼릭 bridge — `agent_root` 로 `-C` 를 잡으면 샌드박스 workdir 이 agent_root 로 확장되어 실행 중인 에이전트가 **자기 자신의 AGENTS.md / skills / .codex/config.toml 을 덮어쓸 수 있게** 됨. Materializer 가 narrow exception 으로 `workspace/AGENTS.md` 심볼릭을 관리하게 해서 샌드박스는 workspace/ 로 좁히면서 codex 가 cwd 에서 AGENTS.md 발견.
4. `91a6785` `workspace/AGENTS.md` 를 `agents_md=None` 경로에서도 reconcile — 이전 spawn 의 심볼릭이 prune 이후 dangling 상태로 남는 버그.

**Phase 3 Claude Code 어댑터 (`e707476`)**:
- `doorae-sdk/doorae_sdk/integrations/claude_code.py` 를 conceptual stub 에서 실제 `claude-agent-sdk` 드라이버로 재작성. `ClaudeAgentOptions(cwd=str(Path.cwd()), setting_sources=["project"])` — 두 필드 모두 비가역. `setting_sources` 가 `None` (기본값) 이면 CLAUDE.md 와 프로젝트 스킬이 조용히 무시됨. 단위 테스트에서 정확 값을 핀.
- 룸별 `resume` 세션 유지. `_last_session_id` 를 쿼리 drain 중 포착해 handler wrapper 가 room 맵에 반영.
- `_collect_reply` 가 `AssistantMessage.TextBlock` 만 추출. `ToolUseBlock` / `ToolResultBlock` 의 `text` 는 **skill 파일 본문이 leak** 되는 첫 실측 버그의 근원이었음. 메시지 타입과 블록 타입을 이름으로 필터링. `ResultMessage.result` 가 있으면 그걸 우선 사용.
- Detector fix: Claude Code 바이너리 이름이 `claude` (not `claude-code`). `BINARY_ENGINES = [("claude-code", "claude"), ...]`.
- Materializer 에 `workspace/CLAUDE.md → ../CLAUDE.md` 심볼릭 추가 (AGENTS.md bridge 와 동일 패턴, 양방향 reconcile).
- `claude-code-sdk` → `claude-agent-sdk` 의존성 전환.

**Phase 1.5 SKILL.md auto-inline (`c4beeeb`)**:
- Phase 1 실측에서 "Codex 는 파일 기반 skill discovery 를 안 한다" 는 한계 발견. SKILL.md 파일이 있어도 codex 가 자동 로드하지 않아 admin 이 AGENTS.md 에 수동으로 스킬 규칙을 나열해야 했음.
- `Spawner._compose_agents_md(msg)` 헬퍼가 base AGENTS.md 뒤에 `## Available skills` 섹션을 자동 append. 모든 `skills/*/SKILL.md` 본문을 path 정렬 순서로 concatenate.
- **실측 검증**: AGENTS.md 에서 `## 가용 스킬` 섹션을 제거한 최소 버전으로 seed. Codex agent 가 자동으로 스킬을 인식하고 `[SKILL: greeting]`, `[SKILL: time-check]` 규칙을 준수한 응답 반환.
- Claude Code 는 `.claude/skills/` 네이티브 discovery + AGENTS.md 인라인 둘 다 볼 수 있음 (경미한 중복, 무해).

**실측 (Playwright)**:
- Codex 엔진: greeting + time-check 스킬 모두 정확한 포맷으로 응답
- Claude Code 엔진: `.claude/skills/*/SKILL.md` SDK-native discovery 로 동일한 응답
- Codex 엔진 + AGENTS.md 에 스킬 nameOnly (Phase 1.5): 여전히 스킬 규칙 준수

**Codex stop-hook 7건 전부 해결**: (1) 빌드 산출물 tracking, (2) manifest delete 반영, (3) agent_id path escape, (4) `-o` 출력 경로 sandbox 밖, (5) `-C` 누락, (6) `-C agent_root` sandbox 확장, (7) `workspace/AGENTS.md` dangling.

### 2026-04-12 — Phase 1 완료 (Codex workspace-write + Gemini CLI 신규)

브랜치: `feat/per-agent-directory-skills`

- **Codex 어댑터 (`fe291c5`)** — Phase 0 의 materializer 가 cwd=workspace 를 보장하므로 기존 `-C <tempdir>` + `mkdtemp` 조합 제거. 이제 codex 가 자연스럽게 부모 디렉토리 (`~/.doorae/agents/<id>/`) 에서 AGENTS.md 를 자동 탐색. Sandbox 기본값 `danger-full-access → workspace-write` 로 강화. "sandbox 가 workspace-write 여야 한다" 는 보안 계약을 단위 테스트로 고정 (안전 regression 방지).
- **Gemini CLI 어댑터 (`fe291c5`)** — `doorae-sdk/doorae_sdk/integrations/gemini_cli.py` 신규. Codex 어댑터와 쌍둥이 subprocess 패턴: `gemini -p <prompt> --output-format json`, 룸별 대화 컨텍스트, 120s 타임아웃. `_parse_response` 는 response/text/content/output 키를 순서대로 탐색하고 JSON 실패 시 raw fallback (gemini CLI 의 JSON 스키마가 버전마다 달라서 느슨하게 파싱하는 게 안전함). Empty response/exception 시 conversation rollback 으로 다음 호출에서 유령 user turn 재전송 방지. 13 new tests + 7 parse_response cases.
- **Engine registry wiring** — `ENGINES["gemini-cli"]`, `_ADAPTER_CLASSES`, SDK CLI `_setup_engine()` 분기, `doorae-machine` `BINARY_ENGINES` 에 `("gemini-cli", "gemini")` 추가. 머신이 register 시 gemini 가 설치돼 있으면 capability 로 보고.
- **Agent directory 탐색 breadcrumb** — 두 어댑터의 `start()` 가 `Path.cwd().parent / "AGENTS.md"` 존재 여부를 info log 로 남김. "스킬이 왜 안 로드되지?" 디버깅 첫 신호.

**결과**: SDK 53 tests (+15), 머신 105, 서버 198, 총 356. 구조적으로 Codex + Gemini CLI 는 매우 유사한 패턴이라 Phase 2 (Deep Agents) 와 Phase 3 (Claude Code) 도 비슷하게 진행 가능할 것. MCP 서버 설정 같은 per-engine config 는 어댑터 코드가 아니라 `.codex/config.toml` / `.gemini/settings.json` 파일로 `agent_files` 에 저장되므로, 선언적 모델이 유지됨.

### 2026-04-12 — Per-agent directory Phase 0 구현 완료

브랜치: `feat/per-agent-directory-skills` (아직 main 머지 전)

Phase 0 8개 서브태스크 전부 완료. 머신 데몬이 spawn 프레임 하나만 받으면 `~/.doorae/agents/<agent_id>/` 트리를 manifest 에 맞게 prune + reconcile 하도록 정착.

- **공유 path validation** — `doorae-machine/doorae_machine/agent_dir.py` + `doorae-server/doorae/agent_files.py` 양쪽에 동일한 화이트리스트 (prefix, 확장자, 깊이 6, 길이 512). Null/control char, `..`, 절대경로, `workspace/*` 전부 거부. 34 tests × 2. `1b165e5`
- **`SpawnAgentFrame` 확장** — `agents_md`, `files: dict[str,str]`, `engine_secrets: dict[str,str]` optional 필드 추가. 기존 `profile_yaml` 경로는 backward compat 로 유지. `6791751`
- **DB 확장** — `agents.agents_md` Text 컬럼 + `agent_files` 테이블 (UNIQUE(agent_id, path), CASCADE). Alembic revision `005`. 4 새 model tests. `5cbed62`
- **`Spawner._materialize_agent_dir`** — **declarative reconcile** 핵심 구현. workspace/ 제외 모든 managed 트리 prune → AGENTS.md + files 재작성 → CLAUDE.md/`.agents/skills`/`.claude/skills` 심볼릭 재생성 → engine_secrets 을 `.gemini/.env`/`.codex/.env`/`.claude/.env` 로 렌더링 → workspace/ 보장. 13 materialize tests (fresh create, prune 제거, workspace 보존, 엔진 config prune, 심볼릭 cleanup, path validation 거부). `8a93014`
- **`Spawner.spawn` cwd 연결** — `create_subprocess_exec(..., cwd=<agent_root>/workspace)` 로 기동해서 엔진의 cwd 상향 탐색이 materialized 파일 자동 발견. `8a93014`
- **`AgentLifecycle.request_start` 확장** — DB 에서 `agents.agents_md` + `agent_files` 로드해 spawn 프레임에 실어 보냄. 2 새 lifecycle tests (manifest 전송 + legacy backward compat). `1ed9b84`
- **Cross-package E2E smoke test** — 서버 → 프레임 → 머신 materialize 파이프라인을 Python 레벨에서 검증. Prune 시나리오 (`skills/reviewer` 삭제 후 re-spawn 해서 디스크에서 사라지는지) + workspace 보존 (spawn 사이에 scratch 파일 살아있는지) 두 가지 핵심 계약 테스트. `8e45f3d`
- **Codex 리뷰 반영 — declarative reconcile 규칙 명시** — 원래 설계는 files 를 "추가만" 하는 형태라 admin 이 DB 에서 스킬 삭제해도 디스크에 남는 유령 상태 버그가 있었음. plan doc + ADR-002 에 prune 의무 규칙 명시. `53c5095`

### 2026-04-11 — Agent interaction pipeline repair + per-agent directory design

`feat/impl-doorae-chat-server` → `main` (PR #2 머지, merge commit `7f232de`)

- **Agent dial-back URL 파이프라인 수정** — 에이전트가 엉뚱한 서버(옛 워크트리 :8000)에 붙어 룸 메시지를 못 받던 버그를 4단계로 추적해 수정. 머신 데몬이 자기 연결 URL 의 origin 을 agent `--server` 로 고정 (0720e94) + CLI `--host`/`--port` 를 `DOORAE_HOST`/`DOORAE_PORT` 로 승격 + `DooraeSettings.reachable_host()` 가 wildcard 바인드 (0.0.0.0/::) 를 loopback 으로 rewrite (ee63a7a) + setdefault 로 operator 환경변수 보존 (bbd3ee6) + 빈 env 를 unset 취급 (315df30) + `/ws/machines/<id>` 접미사만 트림해 리버스 프록시 prefix 보존 (578d429)
- **크래시 루프 방지** — `request_start` 가 rooms=[] 인 에이전트를 거부 (810e02a). `last_crash_reason` 에 사람이 읽을 수 있는 이유 기록
- **좀비 에이전트 reverse-reconcile** — heartbeat 핸들러가 DB 에 running 으로 남은 유령 PID 를 감지해 pending 으로 되돌리고 재스케줄 (6fa4aaa)
- **채팅 뷰포트 안정화** — 타이핑 인디케이터를 고정 높이 슬롯으로 분리해 메시지 영역이 위아래로 밀리는 현상 제거 (ebe1445)
- **레포 위생** — `doorae/static/` (vite 빌드 산출물) untrack + `.gitignore` (45caaf4). `CLAUDE.md` 를 tracked 로 전환 (559c79b)
- **에이전트별 디렉토리 레이아웃 설계 완료** — Codex / Claude Code / Gemini CLI / Deep Agents / OpenHands / (Anthropic raw) / (OpenAI raw) 7개 엔진의 파일 기반 discovery 방식을 조사하고 `AGENTS.md + skills/<name>/SKILL.md` 단일 source of truth 로 수렴하는 레이아웃 확정. [`docs/plans/2026-04-11-per-agent-directory-skills.md`](plans/2026-04-11-per-agent-directory-skills.md), [`docs/decisions/002-per-agent-directory-with-server-manifest.md`](decisions/002-per-agent-directory-with-server-manifest.md)

## Server (doorae-server)

### REST API

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| POST/GET /api/v1/machines | done | api/v1/machines.py | 등록, 목록, drain, token revoke |
| POST/GET/DELETE /api/v1/agents | done | api/v1/agents.py | CRUD + lifecycle 트리거 |
| POST /api/v1/agents/{id}/start | done | api/v1/agents.py | 재시작 API (5a72e1d에서 재적용) |
| POST /api/v1/agents/{id}/stop | done | api/v1/agents.py | 정지 API (923f0f7에서 추가) |
| POST/DELETE /api/v1/agents/{id}/rooms | done | api/v1/agents.py | 동적 룸 관리 (5a72e1d에서 재적용) |
| GET /api/v1/agents/{id}/rooms | done | api/v1/agents.py | 에이전트 룸 목록 (5a72e1d에서 재적용) |
| POST/GET /api/v1/rooms | done | rooms/router.py | CRUD + sub-room + 참여자 |
| PUT /api/v1/rooms/{id}/representative | done | rooms/router.py | 대표 에이전트 지정/해제 (admin) |
| GET /api/v1/rooms/{id}/messages | done | messages/router.py | since_seq 페이지네이션 |
| POST/GET /api/v1/projects | done | api/v1/projects.py | feat/web-ui 브랜치 |
| POST /api/v1/auth/register | done | auth/routes.py | feat/web-ui 브랜치, 첫 유저=admin |
| POST /api/v1/auth/login | done | auth/routes.py | feat/web-ui 브랜치 |
| GET /api/v1/auth/me | done | auth/routes.py | feat/web-ui 브랜치 |
| GET /api/v1/auth/dev-token | done | auth/routes.py | feat/web-ui, DOORAE_DEV=1 전용 |

### WebSocket

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| /ws/rooms/{room_id} | done | ws/handler.py | 메시지, typing, rate-limit, 멘션, room_query 라우팅 |
| /ws/machines/{machine_id} | done | ws/machine_handler.py | 데몬 연결, heartbeat, 상태 복원 |

### 인증

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| JWT 생성/검증 (유저) | done | auth/jwt.py | HS256, 24h |
| API Token (에이전트) | done | auth/token.py | argon2, lookup_hint |
| Machine Token (데몬) | done | auth/machine_token.py | argon2, lookup_hint |
| Identity 의존성 주입 | done | auth/dependencies.py | HTTP Bearer + WS subprotocol |
| Password 해시 | done | auth/password.py | feat/web-ui 브랜치 |

### 스케줄러

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| Bin-pack placement | done | scheduler/placement.py | 엔진 필터, 라벨 매칭, 용량 체크 |
| Agent lifecycle 상태 머신 | done | scheduler/lifecycle.py | pending→starting→running→crashed/stopped. rooms=[] 가드 (810e02a) 추가 — 방 없는 에이전트는 스폰 거부 |
| Machine bus | done | scheduler/machine_bus.py | 활성 WS 연결 풀, async lock |
| Stale 상태 리셋 (서버 시작 시) | done | app.py | feat/web-ui 브랜치 |
| Heartbeat 양방향 reconcile | done | ws/machine_handler.py | forward (daemon→DB 복원) + reverse (DB→pending, 좀비 정리, 6fa4aaa) |

### 오케스트레이션

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| Cooldown 토큰 버킷 | done | orchestration/rules.py | 5 msg/s |
| Typing tracker | done | orchestration/rules.py | 5초 TTL |
| @멘션 파싱 (ID 기반 + 레거시) | done | orchestration/rules.py | `<@user:id>`, `<#room:id>` ID 토큰 + 하위호환 `@Name`. WS handler 가 extra_metadata 에 머지 |
| 룸 대표 에이전트 라우팅 | done | ws/handler.py | `#룸` 멘션 → 대표 조회 → 자동 참여 → room_query metadata 부착 |
| room_query 의견 수집 (SDK) | done | integrations/room_query.py | 복수 에이전트 응답 수집 + 전원 응답/5분 타임아웃 → 취합 결과 전달 |

### 인프라

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| FastAPI factory + lifespan | done | app.py | |
| JWT secret 영속화 | done | app.py | feat/web-ui, ~/.doorae/jwt_secret |
| Dev 모드 자동 admin | done | app.py | feat/web-ui, DOORAE_DEV=1 |
| `reachable_host()` 도우미 | done | config.py | ee63a7a, 0.0.0.0 / :: / "" → 127.0.0.1 로 rewrite (에이전트 dial-back URL 조합) |
| CLI `--host`/`--port` env 승격 | done | cli.py | bbd3ee6, `_apply_runtime_env()` — setdefault 로 operator env 보존, 빈 문자열도 unset 처리 (315df30) |
| Prometheus 메트릭 9개 | done | observability/metrics.py | 선언만, 계측 미적용 |
| structlog 설정 | done | observability/logging.py | |
| SPA StaticFiles 서빙 | done | app.py | feat/web-ui, html=True |
| SPA fallback (/{path:path}) | done | app.py | 5a72e1d에서 재적용 |
| 룸 생성 시 생성자 자동 참여 | done | rooms/router.py | 5a72e1d에서 재적용, 생성자=admin |
| 에이전트 삭제 시 실제 DB 삭제 | done | api/v1/agents.py | 5a72e1d에서 재적용, Participant+Token+Agent 삭제 |
| Alembic 마이그레이션 | done | db/migrations/ | 001_initial + 002_machine + 003_agent_token + 004_nullable_message_participant + 005_agent_files_and_agents_md |
| CLI (doorae-server) | done | cli.py | main, init, migrate |
| CI/CD 워크플로 | done | .github/workflows/ | test.yml + release-pypi.yml |

## SDK (doorae-sdk)

### ChatClient

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| 멀티룸 WebSocket | done | client.py | 룸별 독립 연결 |
| 자동 재연결 | done | client.py | 지수 백오프 1→60초 |
| since_seq 복구 | done | client.py | 재연결 시 놓친 메시지 |
| 콜백 데코레이터 | done | client.py | @on_message, @on_join_room |
| Nonce 기반 자기 에코 필터링 | done | client.py | 무한 루프 방지 |
| sendTyping | done | client.py | 53233e5에서 추가, WS 기반 typing 전송 |

### 엔진 어댑터

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| Codex | done | integrations/codex.py | `codex_app_server.AsyncCodex` 기반 (2026-04-14). 에이전트당 1개 app-server 상주, 방별 thread로 대화 유지. `codex exec` subprocess 대비 ~10x 빠른 응답. 9 tests |
| Gemini CLI | done | integrations/gemini_cli.py | Phase 1 신규 + agent_root cwd/yolo 수정 (2026-04-12). `gemini -p --output-format json --approval-mode yolo`, 룸별 컨텍스트, 느슨한 JSON 파싱. **서브프로세스 cwd 를 `agent_root` 로 고정** — gemini 의 `findProjectRoot` 가 workspace/ 를 프로젝트 루트로 잡아서 `.gemini/settings.json` / `AGENTS.md` 를 놓치는 버그를 우회. 15 tests (신규 regression: cwd+yolo argv 고정). Playwright 실측 완료 (greeting / time-check 둘 다 정확 포맷) |
| OpenAI | done | integrations/openai.py | API 호출, 대화 히스토리, 에러 복구. skill 주입 작업은 보류 (2026-04-11 결정) |
| Anthropic | done | integrations/anthropic.py | Messages API, 히스토리 롤백. skill 주입 작업은 보류 (2026-04-11 결정) |
| Claude Code | done | integrations/claude_code.py | Phase 3 재작성 (2026-04-12). `claude-agent-sdk` 기반, `ClaudeAgentOptions(cwd, setting_sources=["project"])`, 룸별 `resume` 세션, `TextBlock` 필터링으로 tool-use/result leak 차단. 8 tests. Playwright 실측 완료 |
| OpenHands | stub | integrations/openhands.py | conceptual. V0 TOML 설정은 2026-04-01 자로 deprecated, `openhands.sdk` V1 으로 재작성 필요 |
| Deep Agents | stub | integrations/deep_agents.py | conceptual. 재작성 예정 — `FilesystemBackend + skills=[] + memory=["/AGENTS.md"]` |

### 기타

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| Protocol frames | done | protocol/frames.py | 서버와 동일 복사본 |
| Token 로드 | done | auth/token.py | env → CLI → 파일 순서 |
| Profile 로더 | done | profile/loader.py | YAML |
| CLI (doorae-agent) | done | cli.py | 6종 엔진 선택, --profile |
| CLI (doorae-client) | done | cli.py | 텍스트 채팅 클라이언트 |
| Adapter factory | done | integrations/__init__.py | ENGINES dict + get_adapter() |
| CI/CD 워크플로 | done | .github/workflows/ | test.yml + release-pypi.yml |

## Machine (doorae-machine)

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| Daemon WS 재연결 루프 | done | daemon.py | 지수 백오프, CancelledError 처리 |
| Register + 엔진 보고 | done | daemon.py | 재연결 시 즉시 heartbeat 포함 |
| Heartbeat (30초) | done | daemon.py | running_agents 보고 |
| Spawn agent subprocess | done | spawner.py | doorae-agent 또는 uvx fallback. `--server` 는 데몬의 origin 에서 유도 (0720e94), 리버스 프록시 prefix 보존 (578d429) |
| `_base_url_from_machine_url()` | done | daemon.py | `/ws/machines/<id>` 접미사만 트림, `/doorae` 같은 sub-path prefix 는 유지 — 테스트 6건 |
| Kill agent (SIGTERM→SIGKILL) | done | spawner.py | 10초 대기 |
| Process watchdog | done | supervisor.py | exit code + stderr 수집 |
| 엔진 감지 6종 | done | detector.py | binary 3, python 1, env 2 |
| Protocol frames 11종 | done | protocol/frames.py | |
| CLI (register/run/status) | done | cli.py | --server, --token, --machine-id 지원 |
| systemd unit 생성 | done | cli.py | install-systemd-unit |
| Config TOML + token 파일 | done | config.py | chmod 600 |
| uvx SDK 버전 핀 | done | spawner.py | ~=machine minor version |

## Web UI (feat/web-ui → impl 브랜치에 머지됨)

### 페이지

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| 로그인/회원가입 | done | pages/LoginPage.tsx | Tabs 전환, 에러 표시 |
| 채팅 (Slack 레이아웃) | done | pages/ChatPage.tsx | 사이드바 + 메시지 영역 |
| 에이전트 관리 | done | pages/AdminAgentsPage.tsx | 테이블 + CRUD + Start/Stop |
| 머신 관리 | done | pages/AdminMachinesPage.tsx | 테이블 + drain + 등록 |

### 컴포넌트

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| Sidebar (프로젝트/룸 트리) | done | components/Sidebar.tsx | |
| MessageBubble | done | components/MessageBubble.tsx | display_name 표시, 멘션 렌더링, 에이전트 메시지 전체 너비 |
| ChatArea | done | components/ChatArea.tsx | participants 전달, max-w-3xl 중앙 정렬 |
| MessageInput | done | components/MessageInput.tsx | Enter 전송, typing, @멘션/#룸 자동완성 |
| MentionPopover | done | components/MentionPopover.tsx | 멘션 자동완성 드롭다운 (키보드+마우스) |
| LoginForm | done | components/LoginForm.tsx | |
| CreateRoomDialog | done | components/CreateRoomDialog.tsx | |
| AdminAgents | done | components/AdminAgents.tsx | 6종 엔진 (5a72e1d 재적용) |
| AdminMachines | done | components/AdminMachines.tsx | 등록 다이얼로그 + 토큰 표시 (5a72e1d 재적용) |
| RoomHeader | done | components/RoomHeader.tsx | |
| shadcn/ui 기본 11종 | done | components/ui/ | button, card, input, dialog 등 |
| shadcn-chat 컴포넌트 | done | components/ui/chat/ | ChatBubble, MessageList (미적용) |

### Hooks

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| useAuth | done | hooks/useAuth.ts | dev-token 자동 시도 포함 |
| useWebSocket | done | hooks/useWebSocket.ts | 재연결, since_seq, typing, metadata 전송 |
| useRooms | done | hooks/useRooms.ts | projects + rooms |
| useAgents | done | hooks/useAgents.ts | startAgent 포함 (5a72e1d 재적용) |
| useMachines | done | hooks/useMachines.ts | registerMachine 포함 (5a72e1d 재적용) |

### Lib

| Feature | Status | File(s) | Notes |
|---------|--------|---------|-------|
| mentions | done | lib/mentions.ts | parseMentionTokens, insertMentionToken, extractMentionsMetadata |

### 롤백된 기능 상세

| Feature | 설명 | 롤백 사유 |
|---------|------|----------|
| shadcn-chat 컴포넌트 적용 | ChatBubble/MessageList로 채팅 UI 교체 | 5a72e1d에서 명시적 제외 |

> **참고**: 이전에 10개였던 롤백 항목 중 9개가 commit 5a72e1d에서 재적용됨.
> shadcn-chat 컴포넌트 파일은 존재하나 ChatPage에 적용되지 않은 상태.

## Planned (미구현)

### Per-agent directory + 파일 기반 스킬 (Phase 0–4)

설계: [`docs/plans/2026-04-11-per-agent-directory-skills.md`](plans/2026-04-11-per-agent-directory-skills.md)
결정: [`docs/decisions/002-per-agent-directory-with-server-manifest.md`](decisions/002-per-agent-directory-with-server-manifest.md)

| Feature | Phase | Status | 설명 |
|---------|-------|--------|------|
| `agent_files` 테이블 + `agents.agents_md` 컬럼 | 0 | ✅ done | migration 005, 4 model tests |
| `spawn_agent` 프레임 확장 | 0 | ✅ done | agents_md, files, engine_secrets optional 필드 |
| `Spawner._materialize_agent_dir()` with prune | 0 | ✅ done | declarative reconcile, 32 tests |
| subprocess `cwd=workspace/` 전달 | 0 | ✅ done | spawn() 에서 엔진이 AGENTS.md 자동 탐색 |
| path validation (서버 + 머신) | 0 | ✅ done | 화이트리스트 + 경로 탈출 방지, 34+34 tests |
| AgentLifecycle DB → frame 전송 | 0 | ✅ done | backward compat 유지, 2 새 lifecycle tests |
| Cross-package E2E smoke test | 0 | ✅ done | prune + workspace 보존 계약 검증 |
| Codex 어댑터 AGENTS.md 전환 | 1 | ✅ done | `-C workspace/` + `workspace/AGENTS.md` 심볼릭 bridge, tight sandbox 유지, Playwright 실측 |
| Gemini CLI 신규 어댑터 | 1 | ✅ done | `gemini -p --output-format json`, 룸별 컨텍스트, detector 등록 (gemini 바이너리 실측 대기) |
| Codex SKILL.md auto-inline into AGENTS.md | 1.5 | ✅ done | `_compose_agents_md()` 가 `skills/*/SKILL.md` 본문을 AGENTS.md 에 자동 append, Playwright 실측 완료 (`c4beeeb`) |
| Claude Code SDK 어댑터 재작성 | 3 | ✅ done | `claude-agent-sdk` 기반, `cwd + setting_sources=["project"]`, `TextBlock` 필터링, 세션 `resume`, `workspace/CLAUDE.md` 심볼릭, Playwright 실측 완료 (`e707476`) |
| Deep Agents 어댑터 재작성 | 2 | 🔜 next | `FilesystemBackend(virtual_mode=True) + skills + memory + checkpointer` |
| OpenHands V1 SDK 마이그레이션 | 4 | 📅 planned | `openhands.sdk.Conversation(workspace=...)`, microagent generator |
| Agent Protocol 서버 (doorae-machine) | X | 📅 future | Deep Agents `AsyncSubAgent` 가 Doorae 에이전트를 sub-agent 로 호출 가능하게 — 별도 ADR 필요 |
| Admin UI — `agent_files` CRUD | UI | ✅ done | PR #8 (backend `PUT /agents/{id}` + `/agents/{id}/files` CRUD) + PR #9 (`AdminAgents.tsx` + `AgentEditDialog` — AGENTS.md textarea + 파일 트리 그룹핑 + dirty-only save). Playwright 실측 완료 (2026-04-12) |
| `/delegate` 명령 (v1) | — | ✅ done | 메인룸→서브룸 작업 위임. `delegate.py` + 어댑터 3종 + `GET /rooms/{id}/sub-rooms` |
| Delegation 자동 인라인 (v2) | — | ✅ done | `_compose_agents_md()` 에 `## Delegation` 섹션. `rooms.description` + migration 006 |
| WelcomeOut + pending_rooms | — | ✅ done | WS welcome 프레임으로 participant_id + 누락 서브룸 전달 |
| 에이전트 턴 카운터 | — | ✅ done | 에이전트 간 6턴 초과 시 handler 스킵, `[DELEGATED]` 리셋 |
| Room description UI | UI | ✅ done | CreateSubRoomDialog + RoomEditDialog (PATCH /rooms/{id}) |
| `should_respond` 통합 게이트 | — | ✅ done | 멘션/@mention + [DELEGATED] + 사람 메시지 필터. 어댑터 3종 적용. 8 tests |
| 비동기 delegate | — | ✅ done | 콜백 기반 비차단. 5분 safety timeout |
| typing debounce | UI | ✅ done | setTimeout 스택킹 → debounce. flickering 근절 |

### 기타 장기 로드맵

| Feature | 설계 문서 | Notes |
|---------|----------|-------|
| TypeScript SDK | 08-operations.md §Phase 2 | Week 6-7 예정 |
| PyInstaller 바이너리 배포 | 08-operations.md §Phase 3 | Week 8+ |
| Federation (다중 인스턴스) | Plan B/C | 미정 |
| doorae-machine CI/CD | — | .github/workflows/ 미생성 |
| Locust 부하 테스트 | week5 §Phase 5G | 50연결/10msg/s/p99<50ms |
| CHANGELOG.md | week3 §Phase 3D | 3개 패키지 모두 미작성 |
