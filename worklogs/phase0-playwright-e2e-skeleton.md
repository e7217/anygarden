# test(frontend): 결정론적 Playwright E2E 골격 구축 (Phase 0, task #8)

- Commit: 미커밋 (Phase 0 작업)
- Author: QA-E2E에이전트
- Date: 2026-08-05
- Task: task #8

## Situation

프런트엔드는 Vitest 단위·컴포넌트 테스트를 보유하고 있고, 서버에는 REST/WebSocket
통합 E2E와 실제 Codex 스모크가 있다. 하지만 실제 브라우저에서 라우팅, 인증 폼,
세션 영속화, 화면 전환을 함께 검증하는 자동화 경로는 없었다.

## Task

- 결정론적으로 실행 가능한 Playwright E2E 골격을 추가한다.
- Python 서버·DB·실제 LLM 없이도 CI에서 재현할 수 있어야 한다.
- 실패 시 trace·screenshot·video를 CI에서 수집할 수 있어야 한다.

## Action

- `@playwright/test`를 프런트엔드 개발 의존성으로 추가하고
  `npm run test:e2e`/`test:e2e:ui` 명령을 제공했다.
- `playwright.config.ts`가 Vite를 localhost의 고정 포트로 기동하도록 구성했다.
  CI에서는 단일 worker와 재시도 2회, 로컬에서는 병렬 실행과 기존 개발 서버 재사용을
  사용한다.
- 첫 브라우저 스모크는 API route fixture로 인증 계약을 국소 모킹한다.
  - 성공 로그인: 토큰 저장 후 빈 워크스페이스로 이동
  - 실패 로그인: 로그인 화면 유지와 서버 오류 노출
- GitHub Actions 프런트엔드 잡에 Chromium 설치·E2E 실행·실패 시 증적 업로드를
  추가했다.
- Playwright 결과 디렉터리를 gitignore에 등록했다.

## Decisions

- 이 단계의 브라우저 E2E는 백엔드와 실제 모델을 띄우지 않는다. UI 계약의 실패를
  네트워크·DB·LLM 변동성과 분리해야 CI가 결정론적이고 원인도 명확하다.
- 서버/WS 통합 E2E와 실제 엔진 스모크는 기존 계층으로 유지한다. 이후 룸·스레드·작업
  여정은 같은 Playwright fixture 방식을 확장해 구현한다.
- 로그인은 모든 협업 여정의 선행 조건이면서 Vite 단독으로도 검증 가능한 최소 수직
  슬라이스여서 첫 시나리오로 선정했다.

## Result

- `npm run test:e2e` — 2 passed.
- `npm test` — 48 files, 450 tests passed. 기존 jsdom의 navigation 미구현 경고 1건은
  사전 존재 경고이며 결과에는 영향이 없다.
- `npm run build` — typecheck 및 production bundle passed. 기존 500 kB chunk 경고만
  출력됐다.
