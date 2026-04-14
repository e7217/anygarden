# Mention System Design — `@` 참여자 / `#` 채팅방

**날짜**: 2026-04-13
**브랜치**: `feat/mention-system`

## 개요

채팅 메시지에서 `@`로 참여자를, `#`로 채팅방을 멘션하는 기능.
자동완성 드롭다운을 통해 선택하고, ID 기반으로 저장하여 이름 변경에 안전.

## 저장 포맷

### 메시지 content (인라인 토큰)

```
사용자 입력: @홍길동 이거 #프로젝트-A 에서 봐주세요
DB 저장:     <@user:abc123> 이거 <#room:xyz789> 에서 봐주세요
```

- `<@user:{participant_id}>` — 참여자 멘션
- `<#room:{room_id}>` — 방 멘션

### extra_metadata (빠른 조회용)

```json
{
  "mentions": [
    { "type": "user", "id": "abc123" },
    { "type": "room", "id": "xyz789" }
  ]
}
```

기존 `extra_metadata` JSON 컬럼 활용 — DB 마이그레이션 불필요.

## 프론트엔드

### MessageInput 업그레이드

- textarea에서 `@` 또는 `#` 입력 감지
- 커서 위치 기준 드롭다운 팝오버 표시
- `@` → 현재 방 참여자 목록 (ChatPage의 participants map)
- `#` → 전체 방 목록 (useRooms context)
- 실시간 필터링 (타이핑에 따라)
- 선택: 방향키 + Enter 또는 클릭
- 취소: Esc 또는 백스페이스로 트리거 문자 삭제

### 자동완성 데이터 소싱

- 참여자: `GET /api/v1/rooms/{roomId}` 응답에 이미 포함 — 클라이언트 사이드 필터링
- 방 목록: `useRooms` context에 이미 로드 — 클라이언트 사이드 필터링
- 추가 API 호출 없음

### MarkdownContent 확장

- 렌더링 전 `<@user:id>`, `<#room:id>` 토큰 감지
- 유저 멘션 → 스타일링된 span (Notion Blue 배경 tint)
- 방 멘션 → 클릭 가능한 링크 (`/rooms/{id}`로 네비게이트)
- 삭제된 유저 → `@알 수 없는 사용자`
- 삭제된 방 → 비활성 링크

### 파싱 규칙

- 자동완성으로 삽입된 토큰만 유효한 멘션으로 처리
- 사용자가 직접 `<@user:...>` 형식을 타이핑해도 자동완성을 거치지 않았으면 일반 텍스트
- 코드 블록 내부의 토큰은 렌더링하지 않음

## 백엔드

- 기존 `append_message`로 저장 — `extra_metadata.mentions` 클라이언트가 전송
- 서버 사이드 파싱/검증은 1차에서 생략 (클라이언트 신뢰)
- 별도 API 엔드포인트 추가 없음

## 에이전트 연동

1차 범위에서 제외. 기존 `should_respond` 로직 유지.
향후 `extra_metadata.mentions`에 에이전트 ID 포함 시 `should_respond`에서 참조 가능.

## 범위 외 (향후)

- `@all`, `@here` 특수 멘션
- 멘션 알림 시스템 (뱃지, 사운드)
- 에이전트 멘션 기반 `should_respond` 트리거
- 서버 사이드 멘션 검증
- 멘션 검색/필터 API
