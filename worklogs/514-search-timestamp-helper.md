# refactor(rooms): 검색 결과 타임스탬프를 formatMessageTimestamp로 통일 (#514)

- Commit: `c9fec49` (c9fec4981d7ae9027737102e1d6349ffc9905ca4)
- Author: Changyong Um
- Date: 2026-07-03T09:31:14+09:00
- PR: #514 (issue) / #515 (PR)

## Situation

#512에서 채팅 메시지 버블 타임스탬프를 `lib/datetime.ts`의 `formatMessageTimestamp`(공유 파서 `parseServerDate` + '오늘=시각만, 과거=날짜+시각' 포맷)로 통일했다. 그러나 #512 커밋 전 적대적 리뷰가 짚었듯, `SearchDialog.tsx:94`의 전역 검색 결과 타임스탬프는 여전히 `new Date(r.created_at).toLocaleString()`을 사용해 공유 파서(#93 designator 방어)를 우회하고 채팅과 다른 포맷을 렌더했다.

## Task

- 검색 결과 타임스탬프를 `formatMessageTimestamp`로 교체해 파싱·표기를 일원화.
- 채팅과 일관된 표기: 오늘 항목은 시각만, 지난 항목은 날짜 접두(같은 해 `M월 D일`, 다른 해 `YYYY년 …`).
- `created_at`이 빈 문자열/파싱 불가일 때도 안전할 것.

## Action

- `packages/cluster/frontend/src/components/SearchDialog.tsx:4` — `formatMessageTimestamp` import 추가.
- 같은 파일 렌더부 — `{r.created_at ? new Date(r.created_at).toLocaleString() : ''}`를 `{formatMessageTimestamp(r.created_at)}`로 교체. 헬퍼가 NaN/빈 입력에 `''`를 반환하므로 기존 삼항 가드 제거.

## Decisions

- **동일 헬퍼 재사용 vs 검색 전용 신규 포맷터**: 검색은 여러 기간에 걸치므로 "항상 날짜 표기" 변형(`formatSearchTimestamp`)도 고려했다. 그러나 (1) 검색 결과는 최근순으로 나열돼 오늘 항목이 시각만이어도 문맥상 최근임이 분명하고, (2) 사용자가 명시적으로 "동일 헬퍼로 통일"을 승인했으며, (3) 신규 포맷터는 코드·테스트 표면을 늘린다. → 이미 적대적 리뷰·단위 테스트를 거친 `formatMessageTimestamp`를 그대로 재사용.
- **삼항 가드 제거**: `formatMessageTimestamp('')`/파싱 불가 입력은 `parseServerDate` → Invalid Date → `''`로 떨어진다(#512의 `'not-a-date' → ''` 테스트와 동일 경로). 따라서 `r.created_at ? … : ''` 가드는 중복이라 제거.
- **정확성 관점**: #512 리뷰의 검증 결과, 검색 엔드포인트는 서버측 `_fts_created_at_to_iso`가 이미 designator를 붙여 emit하므로 `new Date` 오파싱 위험은 실질적으로 없었다. 이 변경의 주된 가치는 "정확성 버그 수정"이 아니라 단일 파싱·표기 경로로의 **일관성/DRY**임을 명시.
- **가정**: 검색 결과의 today=시각만 표기가 UX상 수용 가능. 스캔성 개선을 위해 '항상 날짜'가 필요해지면 검색 전용 포맷터를 별도 도입(현재 범위 외).

## Result

- 프론트엔드 전체 445개 테스트 통과, `npm run build`(tsc 타입체크) 성공.
- 검색 결과 타임스탬프가 채팅 버블과 동일한 `formatMessageTimestamp` 경로를 통해 렌더 — 파싱·포맷 일원화. 별도 신규 테스트는 추가하지 않음(헬퍼는 #512에서 이미 단위 테스트됨, 이번 변경은 배선).
