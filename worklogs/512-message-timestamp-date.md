# feat(rooms): 채팅 메시지 시각에 지난 날짜 함께 표기 (#512)

- Commit: `16fce50` (16fce505400cd5e5f672cf1a2e1b1101970b2060)
- Author: Changyong Um
- Date: 2026-07-03T09:16:28+09:00
- PR: #512 (issue)

## Situation

채팅방 메시지 버블 하단의 타임스탬프가 '시:분'(예: `14:23`)만 렌더했다. 어제 이전 메시지도 시각만 보여서, 언제 온 메시지인지 스크롤 위치나 문맥으로만 추정해야 했다. 시각 파싱은 이미 `lib/datetime.ts`의 `parseServerDate`(#93 방어 파서)가 담당하고 있었고, `MessageBubble.formatTime`이 그 결과를 `toLocaleTimeString`으로 시각만 포맷했다.

## Task

- 오늘(뷰어 로컬 달력일) 메시지는 기존과 동일하게 시각만 표기 — 외형 불변 보장.
- 지난 날짜 메시지는 날짜를 접두로 표기: 같은 해 `6월 30일 14:23`, 다른 해 `2025년 12월 30일 14:23`.
- '오늘/지난 날짜' 판정은 클라이언트 로컬 타임존 기준 달력일(Y/M/D) 비교.
- 6개 메시지 변형(일반/작업할당/핸드오프/room_query 등) 전부에 일관 적용.

## Action

- `packages/cluster/frontend/src/lib/datetime.ts` — `formatMessageTimestamp(iso, now = new Date())` 신규 추가. `parseServerDate`로 파싱 → NaN이면 `''` 반환(기존 fail-safe 유지) → 시각은 기존 `toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})` 그대로 → `now`와 로컬 Y/M/D 비교로 `sameDay`면 시각만, 아니면 `N월 N일`(다른 해면 `YYYY년` 접두) + 시각.
- `packages/cluster/frontend/src/components/MessageBubble.tsx:21,114` — import를 `parseServerDate` → `formatMessageTimestamp`로 교체하고, `formatTime`을 헬퍼 위임 한 줄로 축약. 6개 호출부(시각 span)는 무변경.
- `packages/cluster/frontend/src/lib/datetime.test.ts` — `formatMessageTimestamp` 단위 테스트 4종 추가(오늘/같은 해 과거/다른 해 과거/파싱 실패). `now`를 메시지 instant의 로컬 성분에서 구성해 CI 타임존과 무관하게 결정적.

## Decisions

계획(`.tmp/plan-512-message-timestamp-date.md`)과 커밋 전 다중 렌즈 적대적 리뷰에서 도출한 근거:

- **인라인 날짜 접두 vs 날짜 구분선(divider)**: divider(Slack/카톡식 경계 행)는 `ChatArea`의 리스트 렌더·핸드오프 숨김·스크롤 앵커 로직과 얽혀 표면적이 크다. 사용자가 "메시지박스 하단 시각에 날짜도 같이"를 명시했고, `formatTime`이 6개 변형의 단일 병목이라 인라인 방식은 함수 본문 한 곳 수정으로 전면 적용된다 → 인라인 채택.
- **포맷 로직 추출 위치**: `MessageBubble` 내부 인라인 확장은 컴포넌트 렌더 없이 테스트 불가하고 `new Date()` 하드코딩으로 날짜 경계 테스트가 어렵다. `lib/datetime.ts`로 추출하고 `now`를 주입 가능하게 해 달력일 분기를 결정적으로 단위 테스트 → 추출 채택.
- **날짜 표기 형식**: 사용자가 한국어 명시 형식(`M월 D일`, 다른 해 `YYYY년 …`)을 선택. `toLocaleDateString('ko-KR', …)`는 `6. 30.`처럼 뒤에 점이 붙어 어색하고, 슬래시(`6/30`)보다 한국어 형식을 선호. 시각은 제품이 Korean-first이고 #512 요구가 "오늘 메시지 외형 불변"이라 기존 `toLocaleTimeString` 호출을 그대로 보존.
- **테스트 TZ 견고성(리뷰 반영)**: 초기 same-day 테스트가 `now`를 메시지 instant와 동일하게 잡아, 구현이 정확 instant 비교로 바뀌어도 통과하는 약한 테스트였음(적대적 리뷰가 확인). `now`를 `d`의 로컬 Y/M/D + `18:07:13`으로 구성 — 어떤 오프셋에서도 같은 로컬 날짜에 머무르되(15분 배수가 아닌 분/초라) instant는 절대 일치하지 않게 하여, mutation 테스트로 정확 instant 비교 mutant가 실제로 죽는 것을 확인.
- **의도적 미채택**: future-dated(시계 스큐로 로컬 익일) 메시지는 날짜 접두 경로로 떨어져 실제 날짜를 표기 — 진실된 출력이고 발생 창이 극히 좁아 그대로 둠. `SearchDialog`의 별도 타임스탬프 포맷 불일치는 범위 외(서버가 이미 designator 포함 emit)로 후속 과제.
- **가정**: '오늘' 기준은 뷰어 로컬 타임존. `now`는 렌더 시점 1회 계산으로 충분(자정 넘겨 열어둔 화면의 실시간 전환은 과설계로 제외). 전제가 바뀌면 재검토.

## Result

- `formatMessageTimestamp` 단위 테스트 4종 추가, 프론트엔드 전체 445개 테스트 통과, `npm run build`(tsc 타입체크 포함) 성공.
- 커밋 전 3개 렌즈 적대적 리뷰(correctness/consistency/completeness) + 검증 라운드 수행: 7개 지적 중 6개 기각(nit/설계 확인/범위 외), 1개(테스트 결정성) 반영.
- 오늘 메시지 외형은 불변, 지난 날짜 메시지는 6개 변형 전부에서 날짜+시각으로 렌더. 후속: `SearchDialog` 타임스탬프 포맷 통일(선택).
