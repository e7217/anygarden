# feat(agent-links): comet while answering, slow gradient when selected (#1047)

- Commit: `5cb1d6f` (5cb1d6f1055b968b840618c8beb248bb6ab29867)
- Author: Changyong Um
- Date: 2026-11-16T11:41:54+09:00
- PR: — (issue #1047)

## Situation

에이전트 overview 맵에서 에이전트를 선택하면 circle의 테두리, 배경, aurora 링, 경로 색상 등 7가지가 한꺼번에 바뀌었다. 또한 answering 상태의 aurora 링과 selection 링이 같은 위치에 겹쳐서 각 신호가 무슨 뜻인지 시각적으로 명확하지 않았다.

## Task

각 UI 신호에 하나의 형태와 위치를 부여한다:
- answering 상태: 꼬리가 사라지고 머리가 밝아지는 comet 링, track 없음
- selected 상태: 6초에 한 바퀴 도는 느린 full aurora gradient; 같은 위치에 두 링을 쌓지 않으므로 선택 시 comet이 사라지고 그 자리를 aurora가 차지함 (카드에는 여전히 answering으로 표시)
- circle 테두리·배경은 선택 시 변경하지 않음; 이름만 bold
- 도달 가능한 경로는 ink 색상; teal은 현재 질문이 흐르는 선에만 사용
- 활성 질문이 있는 선에 세 개의 점(typing indicator)을 midpoint에 표시
- 데스크탑에서 side panel이 열려 zoom이 변경될 때, 선택된 circle이 보이면 pan하지 않고 현재 zoom 유지

## Action

**`AgentLinksCanvas.tsx`**
- `Emphasis` 타입 및 `emphasis` 필드 제거; `ring` 변수(`'select' | 'busy' | null`)로 교체 (74번째 줄 근처)
- `agent-link-node__aurora` 스팬에 modifier 클래스(`is-busy`, `is-select`) 추가; 내부를 glow wrapper + arc span 두 개로 분리해 glow blur가 mask 밖으로 번지도록 구조 변경
- `LinkEdge`에서 `getBezierPath`의 `midX`, `midY` 추출; `data.live === true`일 때 `.agent-link-edge-busy` SVG 배지(세 개 점) 렌더링 (140번째 줄 근처)
- 엣지 색상: `live → teal`, `emphasized → ink`, `그 외 → subtle` (273번째 줄)
- 엣지 data에 `{ live }` 전달 (274번째 줄, 285번째 줄)
- 사이즈 변경 effect에서 선택된 agent node가 viewport 안에 있는지 확인하고, 밖에 있을 때만 fitView; height 변경(모바일 panel stack)이면 기존 로직 그대로 (234~246번째 줄)
- `is-reachable` emphasis 제거; nodes 계산에서 `emphasis` 필드 삭제

**`agentLinks.css`**
- `__aurora` before/after pseudo-element 방식 → `.agent-link-node__aurora > span` 방식으로 교체
- `is-busy`: comet `conic-gradient(0→30deg transparent → 190deg aurora-1 → 360deg aurora-head)`
- `is-select`: 4색 full gradient, `animation-duration: 6s`
- glow span에 `filter: blur(3px)` 적용; arc에만 mask 사용
- `.agent-link-edge-busy` 스타일: rect(배지 배경) + circle(세 점), `@keyframes agent-link-busy`로 순차 점멸
- `.is-selected` 스타일에서 테두리·배경·ring 중복 제거; 이름 bold만 유지
- `.is-reachable` 스타일 삭제

**`index.css`**
- `--color-aurora-head: #06b6d4` (라이트), `#cffafe` (다크) 추가 — comet 머리 색

**`e2e/agent-links.spec.ts`**
- 새로운 comet/selection 링 동작, 경로 색상 변경, edge busy 배지에 대한 E2E 케이스 추가

## Decisions

사용자와 시안(https://claude.ai/artifact/QyWgWtzPjpvFZf54pq4fhM)을 여러 번 주고받으며 정했다. 근거는 `.tmp/plan-1047-overview-selection-aurora.md`.

- **응답 중 표시**: 원 왼쪽 위 배지(제안 3), 혜성 링, teal 테두리를 검토했다. 배지는 작아서 축소하면 약하고 COL-20 배지와 붐볐다. teal 테두리는 선택과 구분이 약했다. 그래서 혜성 링을 택하고, 점 배지는 대화가 실제로 일어나는 연결선 가운데로 옮겼다.
- **선택 링**: 혜성+바탕 트랙, 정지 그라디언트, 6s로 천천히 도는 그라디언트를 검토했다. 혜성은 계속 눈을 끌어 선택 표시로는 과했다. 결정적 근거는 "꽉 찬 원(선택)"과 "머리가 도는 호(응답 중)"가 모양 자체로 구분된다는 점이다.
- **응답 중 + 선택**: 그라디언트 위에 혜성 겹치기, 혜성을 바깥으로 띄우기, 혜성 숨기기를 검토했다. 겹치면 처음 지적한 "실행 중 선택 시 과함"이 다시 생기고, 바깥으로 띄우면 고리가 두 겹이 된다. 선택 시 카드가 항상 "…에서 답하는 중"을 보여주므로 혜성을 숨겨도 정보가 사라지지 않는다.
- **선택 시 카드**: 유지했다. 카드는 룸·알림 링크의 유일한 위치이고, 터치에서는 선택이 유일한 진입이다.
- **지도 재맞춤**: 선택 시 패널을 강제로 열지 않는 안은 #1042의 결정을 뒤집어서 버렸고, 패널을 오버레이로 바꾸는 안은 레이아웃 변경이 커서 버렸다. 남은 안은 데스크톱(폭만 변함)에서 줌을 유지하고 가려질 때만 pan하는 것이다. 모바일은 상세가 아래에 붙어 높이가 줄어드는데, 이때 줌을 유지하면 원이 화면 밖으로 빠지는 것을 기존 `[COL-14]` e2e가 잡아냈다. 그래서 높이가 바뀌면 기존처럼 다시 맞춘다.
- **번짐 레이어**: `filter`와 `mask`를 같은 요소에 두면 mask가 blur를 잘라 번짐이 보이지 않는다(기존 `::after`). glow span에 blur를, 그 안의 arc에 mask를 둔다. 회전은 직계 자식에만 건다. 하위 선택자로 걸면 안쪽 arc가 두 번 돌아 링이 엇갈린다(시안에서 실제로 발생).
- **재검토 신호**: 6s 회전이 너무 안 보이거나 거슬린다는 피드백이 오면 속도를 조정한다. 짧은 연결선에서 가운데 배지가 원과 겹치면 길이 임계값 이하에서 배지를 생략하는 규칙을 검토한다.

## Result

- 에이전트가 answering 중이면 comet 링(빠르게 2초/바퀴), 선택 시 full aurora(느리게 6초/바퀴)로 명확히 구분
- 선택 시 circle 테두리·배경 변화 없음; 이름만 bold
- 도달 가능 경로는 ink, 현재 활성 질문 경로는 teal, 비활성은 subtle로 색상 계층 명확화
- 활성 질문 엣지 midpoint에 typing indicator(점 3개) 배지 표시
- 데스크탑에서 에이전트 선택 시 side panel이 열려도 zoom이 유지되며, 선택된 circle이 가려질 때만 pan 발생
- e2e: 응답 중 혜성·선택 그라디언트·원당 링 하나·경로 잉크색·동작 줄이기 정지(`[COL-19]` 신규), 진행 중 연결선 배지(기존 `[COL-19]` 보강), 패널 다시 열림 시 줌 유지 테스트 추가. agent-links e2e 27/27, vitest 1339/1339 통과
- `docs/requirements.md` COL-19 서술 갱신
