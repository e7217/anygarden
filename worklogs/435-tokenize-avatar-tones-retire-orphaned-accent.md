# refactor(design-system): tokenize avatar tones, retire orphaned accent-* (#435)

- Commit: `3713e82` — Step 3 of #435 migration
- Author: Changyong Um
- Date: 2026-06-12
- PR: #435 (tracking issue)

## Situation

감사는 8색 아바타 팔레트가 `lib/avatar.ts`에 하드코딩(hex + `color-mix`)되어 토큰 레이어 밖에 있고, 동시에 `@theme`에 정의된 `--color-accent-pink/purple/brown` 토큰은 아무도 참조하지 않아 **고아 상태**임을 지적했다. 즉 "아바타용으로 만든 토큰"과 "실제 아바타 색"이 서로 다른 곳에 있어 단일 소스가 없었다.

## Task

- 8색 톤(bg + 초기자 fg)을 `@theme` 토큰으로 승격해 단일 소스화
- `avatar.ts`는 seed→slot 매핑만 유지하고 색은 토큰을 참조
- 고아 `--color-accent-*` 토큰 제거
- 회귀 없이(테스트·렌더) 처리

## Action

- `src/index.css` `@theme`: `--color-tone-1..8` + `--color-tone-N-fg` 8쌍 추가. `--color-accent-pink/purple/brown` 제거.
- `src/lib/avatar.ts`: `PALETTE`의 `bg`/`fg`를 `'var(--color-tone-N)'`/`'var(--color-tone-N-fg)'`로 교체. `ring`(rgba)은 inline 유지. L22–26 주석을 "토큰화됨, seed→slot 매핑만 유지" 취지로 갱신.
- `avatar.test.ts` 무변경(참조 동일성·분포·truthy만 단언 — `var()` 문자열도 truthy).

## Decisions

- **완전 토큰화(채택) vs inline 유지+고아 토큰만 제거**: 사용자 승인. avatar.ts가 색을 inline `style`로 주입하므로 `'var(--color-tone-N)'` 문자열 반환이 그대로 동작 → 토큰화가 seed-매핑 로직을 해치지 않는다. 원작자 주석의 "themable 안 함" 우려는 라이트 모드 단일 테마에선 무력하고, 토큰화는 값의 "위치"만 옮긴다.
- **`ring`은 토큰화하지 않음**: 제안 diff에 ring 토큰이 없고, ring은 표면색이 아니라 파생 focus/presence 액센트라 inline rgba 유지.
- **결정적 근거**: 빌드 결과 `color-mix()`가 정적 hex로 사전 계산되어(tone-2 `#d9eded` 등) 구형 브라우저 호환까지 개선됨 — 토큰화의 부수 이득.
- **가정**: Tailwind v4 `@theme`가 var()-only 토큰을 `:root`로 방출(확인: 빌드 CSS에 16개 값 모두 존재).

## Result

- `npm run build` 통과. avatar.test.ts(14) + EntityAvatar.test.tsx(20) 통과. 빌드 CSS에 톤 토큰 16개 값 방출 확인.
- 아바타 팔레트가 `index.css` 한 곳에 집중, 고아 accent-* 제거. EntityAvatar/EngineGlyph/avatar-options는 인터페이스 무변경이라 그대로 동작.
