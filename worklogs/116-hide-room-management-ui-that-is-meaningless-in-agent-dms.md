# feat(rooms): hide room-management UI that is meaningless in agent DMs (#116)

- Commit: `4e85dab` (4e85dab12a70534308d6659d85760b9ec1d5c01d)
- Author: Changyong Um
- Date: 2026-04-18T22:59:41+09:00
- PR: #116

## Situation

에이전트 DM (`currentRoom.is_dm === true`) 은 유저 1 + 에이전트 1 의 고정
1:1 구조지만, `RoomHeader` 는 일반 방용 컨트롤(참여자 토글, representative
select, `agents N/N` 배지, Manage agents / Create sub-room / Edit room /
Invite links 메뉴) 을 그대로 노출했다. 이 중 `agents 1/1`, 단일
representative select 등은 의미 없는 정보만 화면을 채우고, Invite links 는
제3자 추가로 DM 의 정의 자체를 깬다. UX 레벨에서는 사용자가 유도당하는 ghost
기능이고, 서버 레벨에선 최종 authority 가 이미 막지만 UI 단계에서 미리
거르는 것이 맞다.

## Task

- 에이전트 DM 방에서 비의미적 / DM 정의를 깨는 컨트롤을 숨긴다.
- 자식 컴포넌트 (`RoomHeader`, `RoomSettingsMenu`) 의 기존 "prop=undefined
  → 숨김" 계약을 바꾸지 않는다 (재사용성 유지).
- 일반 방에서의 기능 / 권한 gating (admin vs 멤버) 은 회귀 없이 유지.
- 추후 자식 컴포넌트 리팩터로 계약이 깨져 이 변경이 무효화되지 않도록 계약
  수준의 단위 테스트 스위트를 신규로 추가.

## Action

- `packages/cluster/frontend/src/pages/ChatPage.tsx:86-95` — `currentRoom`
  useMemo 바로 뒤에 `const isDm = !!currentRoom?.is_dm` 지역 상수 추가
  (null 가드 `!!`).
- 같은 파일 389-479 — `<RoomHeader>` prop composition 에 `isDm` 분기 삽입:
  - `participantCount` / `agentsOnline` / `agentsTotal` / `agentParticipants`
    / `onSetRepresentative` / `onManageAgents` / `onCreateSubRoom`
    / `onEditRoom` / `onManageInvites` / `onToggleParticipants` → DM 이면
    `undefined`.
  - `isDm`, `dmAgent`, `onStopAllAgents`, `onDeleteRoom`, `onOpenSidebar`
    등 DM 에서도 의미 있는 prop 은 기존 로직 그대로 유지.
- 동 파일 481-492 — `<ParticipantListPopover>` 를 `{!isDm && (...)}` 로
  감싸 DM 에서 마운트 자체를 스킵. 토글 진입점이 사라진 뒤 `participantsOpen
  === true` 잔존으로 다른 방 전환 시 팝오버가 재오픈되는 엣지 케이스 차단.
- `packages/cluster/frontend/src/components/RoomHeader.test.tsx` 신규 —
  7 케이스:
  - baseline: 모든 handler 주입 시 모든 컨트롤 렌더.
  - `participantCount === undefined` → 토글 부재.
  - `agentsTotal` / `agentsOnline === undefined` → liveness 배지 부재.
  - `onSetRepresentative === undefined` → select 부재.
  - `agentParticipants === []` → select 부재.
  - DM 시뮬레이션: sub-room / edit / invites / manage-agents handler 만
    빼도 Stop all / Delete row 는 그대로 남음.
  - 모든 action handler 제거 시 `…` 트리거 자체 부재.

## Decisions

원본 계획 `.tmp/plan-116-dm-room-header-controls.md` 의 결정 과정을 따랐다.

- **가드 위치** — 호출부(`ChatPage`) 에서 prop 을 `undefined` 로 넘기는
  안 (A1) 채택. 자식 컴포넌트가 `isDm` 분기를 아는 안 (A2) 은 "값 기반
  숨김" (기존) 과 "상태 기반 숨김" (신규) 이 한 컴포넌트에 공존해 이
  요소가 안 보이는 이유를 추적할 때 두 곳을 다 봐야 하는 복잡도를 낳음.
  신규 `<DmRoomHeader />` 를 분리하는 안 (A3) 은 공유 하위 요소가 많아
  분리 비용이 이득을 초과. `ChatPage` 는 이미 `user?.is_admin` 기반 prop
  composition 을 하고 있어 `isDm` 가드도 동일 패턴 확장으로 인지 부담이
  낮다.
- **DM 판별 값 정의** — 지역 상수 `const isDm = !!currentRoom?.is_dm` (B1)
  채택. 인라인 반복 (B2) 은 오타 / 타입 좁힘 실수 증가, `useMemo` 번들
  (B3) 은 단일 boolean 에 과함.
- **ParticipantListPopover 처리** — DM 에서 렌더 스킵 (C1) 채택. 마운트를
  유지하고 토글 진입점만 제거 (C2) 는 `participantsOpen` 잔존으로 다음
  방 전환 시 부작용 가능.
- **테스트 범위** — RoomHeader 단위 테스트 신규 작성 (D1) 채택. 현재
  `RoomHeader` / `RoomSettingsMenu` 단위 테스트 부재 → 계약 보호 투자
  비용이 작고 이득이 큼. ChatPage 통합 테스트 (D3) 는 WS / useRooms 다수
  mock 필요해 ROI 낮음.

**가정** — `currentRoom.is_dm` 은 서버가 신뢰 가능한 소스. admin 이 타인
DM 을 열어보는 기이한 경로가 생기지 않는 한 동일 규칙 적용.
`representativeAgentId` 는 DM 에서도 읽기 전용으로 계속 흐르지만 `onSet`
이 `undefined` 라 select 미렌더 → 현재 RoomHeader 내 다른 소비자 없어
무해.

**위반 시 재검토 트리거** — 자식 컴포넌트가 "undefined → hide" 를 깨거나
(예: default 렌더로 전환), RoomHeader 에 새 DM 무관 컨트롤이 추가될 때.
전자는 새 스위트(7 케이스) 가 알람. 후자는 호출부에 가드 한 줄 더 추가해야
함을 리뷰어가 잡아야 한다.

## Result

- 프론트엔드 테스트: 22 파일 / 214 개 통과 (신규 RoomHeader 7 개 포함).
- `npm run build` 통과.
- 백엔드 영향 없음 — 서버의 invite / sub-room / edit 권한 로직은 그대로
  authority 유지, UI 는 유도하지 않는 차원의 숨김.
- DM 방에서 숨겨지는 요소: participants 토글 / agents liveness 배지 /
  representative select / settings 메뉴의 Create sub-room · Edit room ·
  Invite links · Manage agents / ParticipantListPopover 마운트.
- DM 방에서 유지되는 요소: Connected 배지, Stop all agents (admin), Delete
  room (owner), DM 에이전트 아바타, 룸 이름, 햄버거.
- 시각적 스모크는 워크트리 환경 제약상 미수행 — 병합 후 main 에서 확인 예정.
