# feat(frontend): render ANSI escapes inside fenced code blocks (#290)

- Commit: `b867b8a` (b867b8a58bc1442cb681eb776d214c7263a8e49f)
- Author: Changyong Um
- Date: 2026-04-28T00:12:40+09:00
- PR: #290 (issue)

## Situation

테스트룸4에서 codex가 사용자의 "스샷 찍어달라" 요청에 Conway's Game of Life
실행 결과를 ANSI SGR 이스케이프 시퀀스가 들어간 마크다운 코드 블록으로 응답했다.
채팅 렌더러는 ANSI를 처리하지 못해 `\x1b[38;5;46m...` 같은 raw 텍스트가 그대로
보였고, 사용자에게는 의미 없는 잡음이었다. 이슈 #290 Phase A는 이 즉시 효과
가능한 frontend-only 갭을 메우는 단계.

## Task

- 마크다운 fenced 코드 블록 본문에 ANSI SGR 이스케이프가 있을 때 컬러/굵게/기울임/
  밑줄/취소선/dim을 시각적으로 렌더링.
- 인라인 backtick 스니펫은 영향 없이 통과.
- 일반 마크다운(제목/본문/멘션)은 회귀 없음.
- LLM 출력은 신뢰할 수 없으므로 HTML 인젝션 회피가 필수.

## Action

- `packages/cluster/frontend/package.json`: `anser ^2.3.5` 의존성 추가, `package-lock.json` 동기화.
- `packages/cluster/frontend/src/components/MarkdownContent.tsx`:
  - `Anser`를 import하고 `defaultComponents.code` 커스텀 렌더러 추가
    (라인 ~57-79). react-markdown v10이 `inline` prop을 제거했으므로
    `isBlockCode`는 children에 `\n`이 있거나 className이 `language-*`이면
    block으로 판정.
  - `ANSI_PATTERN` 정규식으로 SGR 시퀀스 존재 여부를 빠르게 검사 후,
    `Anser.ansiToJson(text, { remove_empty: true })`로 토큰 분해.
  - 각 토큰을 `<span data-ansi-segment="" style={...}>`로 매핑.
    `ansiTokenStyle` 헬퍼가 fg→`color: rgb(...)`, bg→`backgroundColor`,
    decorations 배열을 fontWeight/fontStyle/textDecoration/opacity로 변환.
- `packages/cluster/frontend/src/components/MarkdownContent.test.tsx` 신설(99줄):
  - plain block, 8-color fg(SGR 31), 256-color fg(SGR 38;5;46 — codex 실제 케이스),
    bold decoration, 혼합(plain+ANSI), inline 비활성화, 본문 ANSI 비활성화 7건
  - 회귀: 제목/본문, mention pill 2건
- 검증: `npx vitest run` → 9/9 신규 통과. `npm run test` → 357/357 전체 통과.
  `npm run build` → tsc + vite 빌드 통과.

## Decisions

대안과 트레이드오프(`.tmp/plan-290-agent-artifacts-and-ansi.md` D1):

| 후보 | 장점 | 단점 |
|------|------|------|
| `ansi-to-html` | 단순, 작음 (~10KB) | `dangerouslySetInnerHTML` 필요 — LLM 출력에 HTML 인젝션 회피 부담 |
| `anser` | JSON 토큰 → React span 매핑 (XSS 자체 차단) | 약간 큼 (~15KB), API 학습 |
| `xterm.js` | 완전한 터미널 에뮬레이터 | 80KB+, 채팅용 오버킬, 인터랙티브 의존성 |

**선택: anser.** 결정적 근거 — 채팅 메시지는 신뢰할 수 없는 LLM 출력이고
ANSI 시퀀스 사이에 임의 텍스트가 섞이므로 HTML 인젝션 회피가 필수. JSON →
React 노드 매핑은 react-markdown의 다른 컴포넌트 처리 방식과 일관됨. xterm.js는
이번 use case에 비해 너무 무겁고, ansi-to-html은 escape 옵션을 켜도
`dangerouslySetInnerHTML` 단계에서 추가 검증이 필요해 표면적이 늘어난다.

부수 결정:
- **인라인 코드는 의도적으로 색 입히지 않음** — prose 사이의 backtick 스니펫에
  ANSI가 끼어드는 것은 드물고, 인라인까지 처리하면 사용자가 의도하지 않은
  색이 본문에 섞일 수 있어 신뢰도가 떨어진다. 차후 inline에 ANSI가 자주
  보이면 재검토.
- **인터랙티브 ANSI(커서 이동, 화면 클리어)는 비범위** — 1차에서는 색상만.
- **Phase B(에이전트 → 룸 산출물 채널)는 분리 PR** — 이슈는 단일이지만
  변경 면적/리스크가 한 자릿수 차이라 Phase A를 단독 머지 가능 단위로 분리.

가정: codex/claude-code가 보내는 ANSI는 표준 SGR 범위(8/16/256/truecolor + 기본
decoration). 비표준 시퀀스가 자주 보이면 재검토.

## Result

- 테스트룸4의 codex Game of Life 메시지 등 ANSI가 들어간 fenced 코드 블록이
  컬러 그리드로 렌더링됨. SGR 31, 256-color, decoration 케이스 단위 테스트로 검증.
- 일반 마크다운/인라인 코드/멘션 회귀 없음 (357 테스트 그린).
- Phase B(에이전트 → 룸 산출물 채널)는 후속 PR로 진행 예정.
  D8(룸 라우팅 정책)과 quota 정책은 1차 구현 후 사용자 피드백으로 확정 필요.
