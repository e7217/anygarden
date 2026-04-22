# feat(ui): render orchestrator [HANDOFF] messages as breathing-border cards (#238)

- PR: #238
- Date: 2026-04-21
- Branch: `feat/238-handoff-breathing-border`

## Situation

Orchestrator 전략 방에서 Claude가 발행하는 `[HANDOFF] <@user:target-pid> …` 메시지, 직후 발행하는 보조 안내("마이크 넘겼습니다 🎤", "응답을 기다리고 있습니다… 🎤"), 그리고 Codex/Gemini CLI 워커가 답변 말미에 붙이는 `handoff_to: …` trailer가 **프로토콜 잡음인 채로 채팅 UI에 그대로 노출**되고 있었다. 실제 테스트룸4 시연 세션에서 세 메시지 세트(seq=9 handoff → seq=10 상태 안내 → seq=11 trailer 포함 응답)가 반복적으로 나타나면서, 사용자는 LLM이 프레임워크 내부에 대고 말하는 느낌을 받았다.

서버 측 `_apply_orchestrator_handoff` 계약은 이미 안정적으로 돌아가고 있었기 때문에, 이슈 범위를 **프론트엔드 렌더링 교체**로 제한했다. 서버는 그대로 두고 UI가 이 세 가지 잡음을 사람에게 보기 좋게 정돈한다.

## Task

1. `[HANDOFF]` 메시지를 별도 카드(`HandoffMessageCard`)로 분기 렌더링 — 본문의 프로토콜 토큰은 감추고, caption("→ 타겟 에이전트")과 접힌 지시문으로 대체.
2. 카드 상단에 **1px Notion Blue accent 선 + 좌→우 sweep 애니메이션**(2.4s)으로 "타겟 에이전트의 응답을 기다리는 중"을 표현. 타겟이 답하면 정적 accent로 고정, 5분 경과 시 회색으로 페이드.
3. Orchestrator가 같은 사람이 10초 내 발행한 "마이크 넘겼습니다 / 기다리고 있습니다 / 전달하겠습니다" 형태의 상태 안내는 UI에서 숨김.
4. 에이전트 발화 본문 말미의 `handoff_to: …` 또는 "마이크 넘기겠습니다" trailer를 렌더 시점에 strip(wire body는 보존).

서버/프로토콜/DB 무변경 — 프론트엔드 단독 변경이라 범위가 제한적이고 회귀 위험이 낮다.

## Action

### 신규 모듈

- `packages/cluster/frontend/src/lib/handoff.ts` — `room-query.ts`와 같은 순수 헬퍼 스타일로 작성.
  - `parseHandoff(msg)`: content prefix + `metadata.next_speaker_participant_id` + `metadata.mentions`에 `type='user'` 엔트리가 모두 있을 때만 `HandoffMeta` 반환. 세 조건 중 하나라도 빠지면 `null` — 레거시/부분 payload는 기본 메시지 경로로 폴백.
  - `stripHandoffPrefix(content)` / `stripHandoffToTrailer(content)`: 끝 앵커(`$`) 붙은 정규식으로 mid-body 오스트립 방지. Korean variant("X, 마이크 넘기겠습니다.")도 별도 패턴으로 처리.
  - `isHandoffStatusMessage(content)`: Claude 상태 안내 3개 패턴(마이크 넘겼습니다 / 기다리고 있습니다 / 전달하겠습니다)만 매칭. 단독으로는 hide 판정 근거로 부족 — 발신자 + 시간 창을 추가로 요구.

- `packages/cluster/frontend/src/components/HandoffMessageCard.tsx` — `memo`로 감싼 경량 presentational 컴포넌트.
  - props: `{ handoff, targetName, createdAt, resolvedAt, resolveUser?, resolveRoom? }`.
  - 상태(`pending` / `resolved` / `timeout`)는 `resolvedAt`과 `HANDOFF_TIMEOUT_MS=5분`으로 `deriveState`가 계산. 각 상태마다 `handoff-card--{state}` 클래스를 부착해 CSS가 accent를 바꿈.
  - 지시문 영역은 **기본 접힘**(DESIGN.md §4 "낮은 시각 부담" 원칙). `instruction`이 공백이면 토글 자체를 감춰 "보기 버튼이 아무것도 열지 않는" 어색함을 제거.

### CSS

- `src/index.css` 말미에 `@keyframes handoff-sweep`(−100% → 200% background-position)과 `.handoff-card`/`.handoff-card--*` 룰을 추가. accent는 카드 `::before` pseudo-element에 1px 높이로 그려 실제 `border-top`과 충돌하지 않게 함.
- `prefers-reduced-motion: reduce` 미디어 쿼리에서 sweep 애니메이션 중단 + 중앙 고정 위치로 전환 — 접근성 가이드 준수.

### MessageBubble 통합

- `parseHandoff` 결과가 non-null이면 기존 `resultMeta` / `forwardMeta` 분기보다 **먼저** `HandoffMessageCard`로 위임. 카드 자체는 full-width, 상단에 발신자 아바타+이름+시간만 얇게 유지 — 행 정렬이 다른 메시지와 깨지지 않도록.
- 에이전트(`isAgent === true`) 발화의 기본 본문 렌더링 직전에 `stripHandoffToTrailer(message.content)`를 적용. 사용자 본인(`isMine`) 경로는 그대로 — trailer는 워커 응답에만 붙으므로.
- `MessageBubbleProps`에 `handoffResolvedAt?: string | null` 추가. `ChatArea`가 계산해 넘겨줌.

### ChatArea 통합

- `messages` 배열에 대해 **단일 O(n) sweep**으로 두 가지 상태를 동시에 계산하는 `useMemo` 추가:
  - `handoffResolvedMap: Map<messageId, resolvedAt>` — 각 handoff 메시지에 대해 forward-scan으로 "타겟 participant가 발신한 첫 subsequent 메시지의 `created_at`"을 찾아 저장.
  - `hiddenMessageIds: Set<messageId>` — `isHandoffStatusMessage` 패턴에 매칭되고, 같은 발신자가 10초 내 발행한 handoff가 직전에 존재하는 메시지를 숨김 대상으로 등록.
- 렌더 루프에서 `hiddenMessageIds.has(msg.id)`이면 `null` 반환(행 자체를 생략해 여백도 제거), 나머지는 `handoffResolvedMap.get(msg.id) ?? null`을 prop으로 전달.

### 테스트

- `src/lib/handoff.test.ts` (신규, 20개 케이스):
  - `parseHandoff`: 정상 파싱, content prefix 결측, metadata 결측, user mention 없음, 잘못된 브래킷 prefix, mention 토큰 strip, 빈 metadata.
  - `stripHandoffToTrailer`: `handoff_to: <@user:...>` + `participant_id:` 어노테이션, Korean variant, 미드바디 false positive 방지, case-insensitive.
  - `isHandoffStatusMessage`: 3개 패턴 각각 + 일반 메시지/빈 문자열 false.
- `src/components/HandoffMessageCard.test.tsx` (신규, 9개 케이스): 상태별 `data-state` 속성, `handoff-card--*` 클래스, caption, 접힘 기본 + 클릭 펼침, 빈 instruction 시 토글 미표시.
- `src/components/MessageBubble.test.tsx`에 5개 케이스 추가: handoff 카드 위임(프로토콜 토큰 누락 확인), `handoffResolvedAt` prop으로 resolved 전환, metadata 결측 시 폴백, trailer strip 렌더.

## Decisions

### 주요 결정 1: handoff 감지 조건 — 둘 다 충족(C) 채택

계획서 §3.2의 결정을 그대로 따름. `content.startsWith('[HANDOFF]')` 단독(A)은 metadata 결측 시 카드가 터지고, `metadata.next_speaker_participant_id` 단독(B)은 legacy 레코드에 누락될 가능성이 있어 둘 다 있을 때만 handoff로 확정. `_apply_orchestrator_handoff`가 세트로 세 값을 모두 세팅하므로 계약과도 일치.

구현 중 한 가지 추가 발견: 계획서에는 `metadata.handoff` 플래그가 언급돼 있었으나, 실제 `handler.py`는 `metadata["next_speaker_participant_id"]`만 세팅하고 별도 `handoff` 객체는 만들지 않는다. 따라서 구현은 `next_speaker_participant_id + mentions[type=user]` 조합으로 안착. 서버 변경 없이 현재 프로토콜과 정확히 매칭.

### 주요 결정 2: 상태 판정 — forward-scan(A) 채택

O(n) 메시지 루프를 `useMemo`로 감싸 `messages` 배열 identity가 바뀔 때만 재계산. 실제 방에서 n은 보통 수백 이하라 overhead 무시 가능. `#221`이 도입한 `next_speaker_participant_id` 실시간 브로드캐스트(B)는 "현재 턴이 누구인가"를 알려주지만 "각 과거 handoff가 resolved됐는가"는 별개 질문이라 단독으로 쓸 수 없음. 메시지 스캔만 남겼다.

### 주요 결정 3: 상태 안내 숨김 — 클라이언트 패턴 매칭(A) 채택, follow-up으로 B 분리

이번 PR에서는 즉시 UX를 고칠 수 있는 클라이언트 패턴 매칭만 적용. Claude system prompt 강화(B, "자체 상태 안내 금지")는 배포 플로우가 더 크고 LLM이 100% 준수한다는 보장이 없어서, follow-up issue 후보 1번(`feat(orchestrator): constrain claude orchestration output format`)으로 분리.

### 주요 결정 4: 숨김 조건 — 10초 시간 창 + 같은 발신자

계획서는 "발신자가 orchestrator_agent_id와 일치"를 기준으로 제안했으나, `orchestrator_agent_id`를 `ChatArea`까지 threading하려면 `useRooms`/Room 모델에 필드를 추가해야 했다. 구현 비용 대비 정확도 이득이 크지 않다고 판단해 **"직전 handoff와 동일 발신자 + 10초 창"** 프록시로 치환. 실제로 Claude의 상태 안내는 본인이 handoff를 보낸 직후에만 나오므로 이 프록시가 거의 완벽히 일치. 오인 시 한 행이 숨을 뿐이고 원문은 DB에 남아 있어 risk가 낮다.

### 가정 / 재평가 트리거

- 상태 안내 문구가 "넘겼습니다 / 기다리고 있습니다 / 전달하겠습니다" 3종에 수렴한다고 가정. 새 variant가 출현하면 `isHandoffStatusMessage`에 패턴 추가 필요 — 한 곳에 모아두어 리뷰 부담을 줄여둠.
- `HANDOFF_TIMEOUT_MS=5분`은 상수. 탭 백그라운드에서 `setInterval` throttle로 약간 지연돼도 UX상 용납 가능한 범위로 판단. 정확한 전환이 필요하면 `visibilitychange`에서 re-evaluate하는 follow-up이 가능.
- Target 참여자가 handoff와 무관한 다른 메시지를 먼저 보낼 가능성은 현실적으로 매우 낮다고 가정 — 발생 시 해당 메시지가 resolved로 오인되지만 사용자에게 위해는 없음. 필요하면 `in_reply_to_handoff` metadata 추가로 명확화.

## Result

Orchestrator 전략 방의 프로토콜 잡음이 UI에서 제거됨:
- `[HANDOFF]` 메시지는 breathing accent border를 가진 카드로 렌더 → 타겟 응답 시 정적으로 전환, 5분 후 회색 페이드.
- "마이크 넘겼습니다" 계열 상태 안내는 해당 handoff 맥락 안에서 숨김(원본은 DB 보존).
- 워커 응답의 `handoff_to: …` trailer는 렌더 시 strip.

### 테스트 결과

- Frontend unit: **324 passed (30 files)** — 신규 `handoff.test.ts` 20건, `HandoffMessageCard.test.tsx` 9건, `MessageBubble.test.tsx` 확장 5건 포함.
- Frontend build (`tsc -b && vite build`): 경고 없이 성공.
- Cluster pytest: **699 passed, 1 deselected**(slow 마커 제외) — 서버 무변경이지만 회귀 확인 완료.
- Agent pytest는 이번 PR 범위 밖(프론트엔드 단독 변경). 사전 존재하는 `test_openai::test_integrate_registers_handler`(OPENAI_API_KEY 누락) 실패는 #233 worklog에도 기록된 대로 main에서도 재현되는 기존 이슈 — 본 PR과 무관.

### Deferred / Follow-ups

1. `feat(orchestrator): constrain claude orchestration output format` — Claude system prompt에서 `[HANDOFF] @target 지시문` 외 자체 상태 안내·`handoff_to:` 요청 지시를 근본 차단. LLM이 비결정적이므로 UI 폴백은 유지하되 입력단을 좁힘.
2. `feat(ui): admin mode renders handoff instruction expanded by default` — admin/dev 모드에서만 지시문 기본 펼침.
3. 수동 Playwright 검증 — 테스트룸4에서 orchestrator 플로우 재실행, breathing 애니메이션·resolved 전환·trailer 제거·`prefers-reduced-motion` 정적 폴백을 가시 확인. 이번 PR에는 포함 안 함, 리뷰어가 dev 서버에서 검증 권장.
