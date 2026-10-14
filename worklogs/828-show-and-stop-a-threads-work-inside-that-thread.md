# fix(chat): show and stop a thread's work inside that thread (#828)

- Commit: `40042b1` (40042b117135e18100501b9e13e1d38aee580b3e)
- Author: Changyong Um
- Date: 2026-10-14T15:30:22+09:00
- PR: #828

## Situation

에이전트가 스레드 안에서 답변 중일 때 작업 표시기(브레일 스피너)와 개별 stop 버튼이 메인 타임라인 하단에 노출되었다. 이는 스레드와 메인 타임라인의 작업 흐름을 구분하지 못해 사용자가 어느 맥락에서 에이전트가 일하는지 파악하기 어렵고, 잘못된 stop 버튼을 누를 위험이 있었다. 백엔드 `OpenTurnOut` 스키마에도 `thread_root_id` 필드가 없어 프론트엔드가 스레드 귀속 여부를 판단할 수 없었다.

## Task

- `OpenTurnOut` API 응답에 `thread_root_id` 필드 추가 (백엔드)
- 오픈 턴을 스레드별/메인 타임라인으로 분류하는 순수 함수(`openTurns.ts`) 구현
- 스레드 reply affordance 행에 "누가 작업 중"인지 표시하고, 스레드 전체를 한 번에 stop 하는 단일 컨트롤 추가
- 메인 타임라인 하단은 메인 타임라인 귀속 작업만 표시하도록 분리
- `ThreadPanel`에서 에이전트별 개별 stop 컨트롤 목록 노출
- `TurnCancelControl`을 stateless로 리팩터링: `roomId/participantId` 대신 `requestIds[]` + `onCancel` 콜백 수신
- 테스트 커버리지 유지 (신규 `openTurns.test.ts`, 기존 `TurnCancelControl.test.tsx` / `ThreadReplyAffordance.test.tsx` 갱신)

## Action

- **`packages/cluster/anygarden/api/v1/turns.py`** (lines 127–162): `OpenTurnOut.thread_root_id: str | None` 필드 추가; `list_open_room_turns` serializer에서 `turn.thread_root_id` 포함
- **`packages/cluster/frontend/src/lib/openTurns.ts`** (신규): `OpenTurn` 타입, `activeTurnByParticipant`, `placeWorkingParticipants`, `threadTurnIds` 유틸 함수 구현 — UI에 종속되지 않는 순수 로직
- **`packages/cluster/frontend/src/lib/openTurns.test.ts`** (신규): 위 유틸 함수 단위 테스트 31줄
- **`packages/cluster/frontend/src/hooks/useOpenTurns.ts`** (신규): `roomId`와 `workingIds` 변화를 감지해 오픈 턴 목록 fetch/refresh, `cancel(requestIds[])` 콜백 제공
- **`packages/cluster/frontend/src/components/TurnCancelControl.tsx`**: `useEffect` fetch 로직 제거, `requestIds[] + onCancel` props 수신 방식으로 교체 → stateless 컴포넌트화; `text?` prop으로 아이콘+텍스트 조합 지원
- **`packages/cluster/frontend/src/components/TurnCancelControl.test.tsx`**: fetch mock 제거, `onCancel` 콜백 mock 기반 테스트 4개로 재작성
- **`packages/cluster/frontend/src/components/ChatArea.tsx`**: `openTurns`, `onCancelTurns` props 추가; `placeWorkingParticipants`로 main/thread 분기; 메인 타임라인 stop 버튼은 main 귀속 에이전트만; `ThreadReplyAffordance`에 `workingNames`, `stopTurnIds`, `onStop` 전달
- **`packages/cluster/frontend/src/components/ThreadReplyAffordance.tsx`**: `workingNames`가 있으면 reply 건수 없어도 스피너+이름 표시; "Stop all work in this thread" 단일 stop 버튼 추가
- **`packages/cluster/frontend/src/components/ThreadReplyAffordance.test.tsx`**: ENG-09 검증 2개 케이스 추가 (스레드 단일 stop, 메인 타임라인 분리)
- **`packages/cluster/frontend/src/components/ThreadPanel.tsx`**: `workers[]` + `onCancelTurns` props 추가; 에이전트별 스피너·이름·개별 stop 컨트롤 리스트 렌더링
- **`packages/cluster/frontend/src/pages/ChatPage.tsx`**: `useOpenTurns` 연결, `ChatArea`와 `ThreadPanel`에 props 주입
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`**: `chat.threadWorking`, `chat.stopThreadLabel`, `chat.stopThread` 문자열 추가
- **`packages/cluster/tests/test_turn_cancel.py`**: `thread_root_id` 필드 포함 응답 검증 케이스 추가

## Decisions

**`TurnCancelControl`을 stateless로 전환한 이유**
- 기존 설계는 컴포넌트 내부에서 `/api/v1/turns/open`을 fetch하여 `participant_id`로 필터링했다. 스레드 시나리오에서는 여러 에이전트의 턴을 한꺼번에 취소해야 하므로, 컴포넌트가 단일 참가자를 알 수 없는 구조가 필요했다.
- 대안 ①: 컴포넌트에 `participantIds[]` 배열을 받아 내부 fetch 유지 → 컴포넌트가 다시 API를 호출해 "스레드 전체 stop" 버튼마다 네트워크 요청이 중복된다.
- 대안 ②: 부모에서 `requestIds`를 내려주고 `onCancel` 콜백을 위임 → fetch 로직을 `useOpenTurns` 훅 하나로 중앙화할 수 있고, 컴포넌트 테스트에서 fetch mock 불필요. **이 방안 채택.**

**스레드 작업을 reply affordance 행에 표시한 이유**
- 스레드 패널이 항상 열려 있지 않으므로, 패널 없이도 스레드에 작업이 진행 중임을 알 수 있어야 한다.
- 메인 타임라인에 계속 표시하는 방안은 "어느 스레드의 작업인지" 컨텍스트를 잃게 만든다.
- `shownRootIds` 로 현재 타임라인에 reply row가 보이는 스레드만 thread 분기로 처리하여, 보이지 않는 스레드의 작업은 계속 메인 타임라인에 노출된다. (unclear from available context: 스크롤 범위 밖 스레드 처리 정책이 향후 변경될 수 있음)

**`thread_root_id`를 백엔드 OpenTurnOut에 추가한 이유**
- 프론트엔드가 클라이언트 사이드에서 턴의 스레드 귀속을 추론하는 건 불가능하므로, 서버에서 이미 알고 있는 값을 그대로 전달하는 것이 가장 단순하고 안전하다.
- 기존 `turn.thread_root_id` 컬럼은 이미 모델에 존재하여 마이그레이션 없이 serializer만 수정하면 됐다.

## Result

- 에이전트가 스레드 안에서 작업 중이면, 해당 스레드의 reply affordance 행에 스피너와 에이전트 이름이 표시되고, 스레드 전체를 멈추는 단일 stop 버튼이 함께 나타난다.
- ThreadPanel을 열면 에이전트별 스피너와 개별 stop 버튼 목록이 표시된다.
- 메인 타임라인 하단은 메인 타임라인 귀속 작업만 표시하므로, 스레드 작업이 잘못된 위치에 노출되는 문제가 해소된다.
- `TurnCancelControl` 테스트가 fetch mock 없이 작동하여 테스트 안정성이 향상됐다.
- 신규 테스트: `openTurns.test.ts` (유틸 31줄), `ThreadReplyAffordance.test.tsx` ENG-09 케이스 2개, `TurnCancelControl.test.tsx` 4개 케이스.
- Python 테스트(`test_turn_cancel.py`)에서 `thread_root_id` 응답 필드 검증 추가.
