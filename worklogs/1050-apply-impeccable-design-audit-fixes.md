# fix(frontend): apply impeccable design audit fixes (#1050)

- Commit: `d7ae66e` (d7ae66e0538e4497183effcf5be60e207acde742)
- Author: Changyong Um
- Date: 2026-11-18T06:04:02+09:00
- PR: #1050 (issue)

## Situation

impeccable 전체 감사(2026-11-17)에서 프론트엔드는 14/20점을 받았다. 렌더링 검사는 12개 화면을 4가지 폭과 두 테마로 진행했다. 색 토큰 규율은 좋았지만 다음 문제가 남아 있었다.
- 채팅 메시지가 스크린리더에 전달되지 않았다.
- light 보조 텍스트가 AA 대비에 미달했다.
- 직접 만든 입력란의 테두리가 거의 보이지 않았다.
- 일부 버튼은 터치 기기에서 보이지 않았다.
- 진입 청크에 두 언어의 번역 카탈로그가 모두 들어 있었다(gzip 197KB).
- 타입 스케일과 컨트롤 높이 토큰이 넓게 우회됐다. `text-sm/xs`가 676곳, `min-h-11`이 77곳이었다.

## Task

- P1 4건(live region, `--color-foreground-subtle` 대비, 입력란 테두리, 터치 호버 액션)과 P2·P3 권장 사항을 반영한다.
- 렌더링 결과는 바꾸지 않는다. 의도한 변화(12→14px 도움말, 데스크톱 36px 행)만 남긴다.
- 기존 테스트 1339개와 e2e 123개를 깨지 않는다. i18n의 키 타입 검사와 새로고침 없는 언어 전환을 유지한다.

## Action

- **접근성**
  - `ChatArea.tsx`에 polite 상태 영역 두 개를 두었다. 하나는 완료된 새 메시지를, 다른 하나는 작업 중 이름을 읽어 준다. 테스트는 `ChatArea.announcer.test.tsx`다.
  - `MarkdownContent.tsx`는 메시지 안 제목을 두 단계 낮추고 `data-md-level`을 단다. 겉모습은 `index.css`의 `.markdown-prose [data-md-level]` 규칙이 맡는다.
  - title만 있던 아이콘 버튼에 `aria-label`을 달았다. 사이드바 목록을 `<nav>`로 감쌌다.
  - `SearchDialog.tsx`의 포커스 표시를 패널 테두리로 옮겼다. 구분선은 결과 영역에 `empty:hidden`을 걸어 결과가 있을 때만 나온다.
- **색과 토큰** (`index.css`)
  - `--color-foreground-subtle`을 `#5b6d66`으로 바꿨다.
  - 상태 표시에 쓰던 teal을 status 토큰으로 바꿨다: `GoalStatusCard`, `MemoryPanel`, `agentLinks.css`, topology.
  - 팝오버 그림자를 `shadow-deep`/`shadow-card`로 바꾸고, hex 폴백을 제거했다.
- **폼과 터치**
  - CreateSubRoom, RoomEdit, RoomInvite, AdminMCPTemplates, right-rail, TaskPanel, Sidebar의 입력란·선택 상자를 공용 Input/Textarea/Select로 바꿨다.
  - ManifestPanel, TaskPanel, FilesSection의 호버 액션에 `md:pointer-fine:opacity-0` 패턴과 `--control-icon-size`를 적용했다.
  - `RoomEditDialog` 표 컨테이너를 `overflow-x-auto`로 바꿨다.
- **번들**
  - `i18n/locales/{en,ko}.ts`가 각 카탈로그를 `?locale=` 쿼리로 import해 언어별 모듈로 분리한다.
  - `messages.ts`에는 `MessageKey` 타입과 `loadMessages`/`registerMessages`만 남겼다.
  - `main.tsx`가 첫 렌더 전에 활성 언어를 로드한다. 테스트 쪽은 `vitest.setup.ts`가 두 언어를 미리 등록한다.
- **성능**
  - 마크다운 components, 채팅 체인 Map, `FeedbackProvider` value를 메모했다.
  - 이미지 크기를 세션 동안 기억하고, 숨은 탭에서는 폴러(`useMachineLinks`, `useAgents`)와 `BrailleSpinner`를 멈춘다.
  - `SidebarMenuPopover`의 스크롤 리스너를 rAF로 조절했다. `RoomsProvider`는 토큰이 생긴 뒤에 fetch한다.
- **타입 스케일과 높이**
  - `text-sm/xs/base/lg/xl/2xl` 684곳을 caption/badge/body/lead/heading으로 바꿨다. `index.css`의 caption/badge/body 유틸리티에서 굵기를 빼고, 기존 사용처 177곳에 `font-medium`/`font-semibold`를 명시했다.
  - 폼 도움말과 오류 약 70곳을 14px로 올렸다.
  - `min-h-11` 77곳을 `--control-height`/`--control-sm-height`/`--control-icon-size`로 바꿨다.
- **문구** (i18n `agentLinks`, `federation`)
  - 현황 빈 상태(`AgentLinksPage.tsx`)에서 관리자에게 '에이전트 추가'를 보여 준다. 기존 `SidebarCreateAgentDialog`를 재사용한다.
  - `FederationLiveWorkspace.tsx`의 공유 불가 상태를 한 블록으로 합쳤다. `anygarden start --peer-port <포트>`를 안내하고, 세부 내용은 '자세히' 안에 둔다.
- **정리**
  - DESIGN.md §10을 현재 현황 화면(원형 노드, 애니메이션 엣지, comet/선택 링, 상태 배지)에 맞게 다시 썼다.
  - 쓰이지 않던 `components/ui/chat/`를 삭제했다.

## Decisions

- **언어 분리**
  - 검토한 선택지는 세 가지였다. (a) 카탈로그 파일을 언어별로 나누는 것, (b) `?locale=` 쿼리 import로 같은 파일을 언어별 모듈 인스턴스로 읽는 것, (c) 빌드 플러그인을 만드는 것.
  - (b)를 골랐다. 16개 카탈로그의 형태를 바꾸지 않고, 쓰지 않는 쪽 언어 export가 tree-shake된다.
  - 단점은 `federation.ts`처럼 ko/en이 한 객체에 섞인 카탈로그가 양쪽 청크에 다 들어간다는 것이다. 언어당 약 20KB이고, 후속 작업 후보다.
- **타입 스케일 일괄 변환**
  - 단순 치환을 하면 named 유틸리티에 들어 있던 굵기(caption 500, badge 600) 때문에 약 680곳의 평문이 굵어진다.
  - 그래서 유틸리티를 크기·행간·자간만 갖게 바꾸고, 기존 사용처에는 굵기를 명시했다. 행간은 Tailwind 값과 소수점 단위로 같아 바꾸지 않았다.
- **live region**
  - `role="log"`로 목록 전체를 읽게 하면 기록 로드와 페이지네이션 때 한꺼번에 낭독된다. 그래서 숨은 announcer로 새 완료 메시지만 읽게 했다.
  - 화면에 보이는 타이핑 행은 경과 시간이 매초 갱신되므로 live로 두지 않았다.
- **거절한 것**
  - 채팅 행 `content-visibility`: 행 높이 편차가 커서 위로 스크롤할 때 화면이 튀고, 검색 결과로 이동할 때 위치가 어긋난다.
  - 다크 `--color-destructive-foreground` 변경: 실제 사용처는 `danger-solid` 버튼(6.29:1)뿐이라, 바꾸면 그 버튼이 깨진다.
  - skip link: 추가했다가 사용자 판단으로 제거했다. 평소 보이지 않는 키보드 전용 장치라 필요 없다고 봤다.
  - 검색창 포커스 밑줄 단순 삭제: 포커스 표시가 사라져 WCAG 2.4.7에 어긋난다. 대신 패널 테두리로 옮겼다.
- **다시 검토할 조건**
  - Vite가 import 쿼리별 모듈 분리 동작을 바꾸면 언어 분리를 다시 봐야 한다.
  - DESIGN.md가 caption/badge에 굵기를 다시 넣으면 타입 스케일 결정을 다시 봐야 한다.

## Result

- 테스트: tsc 통과, vitest 176개 파일·1360개 통과(기존 1339개에서 늘어남), Playwright e2e 123개 통과, `npm run build` 통과.
- 진입 청크: gzip 197.34KB에서 110.60KB로 줄었다(raw 687KB에서 357KB).
- 같은 조건으로 다시 렌더링 검사했다.
  - 가로 넘침 0, 시작 시 401 오류 없음.
  - 검사기의 low-contrast 경고가 사라졌다. 남은 경고(Inter 폰트, side-tab)는 의도된 선택이다.
- 보이는 변화
  - 데스크톱 사이드바와 목록 행이 44px에서 36px로 낮아졌다.
  - 폼 도움말이 14px로 커졌다.
  - 받은편지함 섹션 제목이 18px에서 20px로 커졌다.
  - 검색 줄이 약 12px 낮아졌다.
- 남은 일
  - `TaskPanel.tsx`는 어디서도 import하지 않는 코드라 삭제 후보다.
  - `federation.ts`를 언어별로 분리하는 후속 작업이 남아 있다.
  - 데이터가 있어야 보이는 화면(터치 액션, 상태 색)은 라이브로 확인하지 않았다.
  - 로그인 화면은 dev 자동 로그인 때문에 렌더링 검사를 하지 못했다.
