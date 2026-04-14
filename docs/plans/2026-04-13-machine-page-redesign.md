# Machine Page Redesign — 좌측 카드 + 우측 상세

**Date**: 2026-04-13
**Context**: 경쟁 서비스 벤치마크 → 머신 중심 에이전트 관리 UX

## 레이아웃

좌측 머신 카드 리스트 + 우측 머신 상세 (에이전트 관리 포함).

```
┌─ Machines ──── [+ Register] ┬─────────────────────────────────────────┐
│                             │                                         │
│  ┌───────────────────────┐  │  gpu-worker-01              ● online    │
│  │ gpu-worker-01         │  │                                         │
│  │ 192.168.1.10          │  │  ┌─ Info ─────────────────────────────┐ │
│  │ ● online   2 agents   │  │  │ Hostname / IP / Version / Engines │ │
│  │ codex, gemini-cli     │  │  │ Capacity / Registered             │ │
│  ├───────────────────────┤  │  └─────────────────────────────────────┘ │
│  │ dev-laptop            │  │                                         │
│  │ localhost             │  │  ┌─ Agents (2) ──── [+ New Agent] ────┐ │
│  │ ○ offline  0 agents   │  │  │ 코딩봇   codex·medium  ● running  │ │
│  │ codex                 │  │  │ 리서치봇 gemini·high   ● running  │ │
│  └───────────────────────┘  │  └─────────────────────────────────────┘ │
│                             │                                         │
│                             │  ┌─ Token & Control ──────────────────┐ │
│                             │  │ [Rotate] [Revoke] [Drain] [Delete] │ │
│                             │  └─────────────────────────────────────┘ │
└─────────────────────────────┴─────────────────────────────────────────┘
```

## 좌측: 머신 카드

- 헤더에 `[+ Register]` 버튼
- 각 카드: 이름, hostname, 상태 (● online / ○ offline / ◑ draining), 에이전트 수, 엔진 목록
- 선택된 카드: 좌측 3px brand border + surface-alt 배경
- 삭제 버튼 없음 (실수 방지 → 우측 상세에서만)

## 우측: 머신 상세

### Info 섹션
Hostname, IP, Daemon Version, Engines (배지), Capacity (N/max), Registered

### Agents 섹션
- 이 머신에 placed된 에이전트 리스트
- 각 에이전트: 이름, 엔진, reasoning effort, 상태, 소속 룸, Stop/Edit 버튼
- `[+ New Agent]` → 다이얼로그 (Engine은 이 머신 엔진만 필터)

### Token & Control 섹션
Rotate Token, Revoke Only, Drain Machine, Delete Machine

## 새 API

- `GET /api/v1/machines/{id}/agents` — 해당 머신의 에이전트 목록
- `GET /api/v1/machines/{id}/engines` — 해당 머신의 엔진 목록

## Agent 생성 다이얼로그 (머신 컨텍스트)

Name → Engine (이 머신만) → Reasoning Effort → Rooms (선택적) → Create

## Agent 탭 변경

- 전체 에이전트 목록은 유지 (모니터링 용)
- "New Agent" 버튼 제거 → 머신 상세에서만 생성
