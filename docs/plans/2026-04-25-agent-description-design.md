# Agent Description 필드 설계

**작성일**: 2026-04-25
**상태**: 설계 완료, 구현 계획 대기
**목적**: 에이전트 간 인식을 이름·UUID에서 의미론적 메타정보까지 확장

## 배경

doorae의 현재 에이전트 인식 메커니즘은 **이름과 UUID만**을 기반으로 한다.

- `Agent` 모델(`packages/cluster/doorae/db/models.py:212-287`)에는 다른 에이전트가 참고할 만한 외부 공개용 설명 필드가 없다.
- WS 프로토콜의 `ParticipantBrief`(`packages/cluster/doorae/ws/protocol.py:182-196`)는 `id`, `display_name`, `kind`, `agent_id`만 노출한다.
- 에이전트 런타임의 로스터(`packages/agent/doorae_agent/integrations/claude_code.py:300-331`, `_compose_participants_roster`)는 `- <@user:{uuid}> {name} ({kind})` 형식으로 LLM에 주입한다.
- 멘션 라우팅(`packages/cluster/doorae/orchestration/rules.py:107-130`)은 명시적 `@` 멘션 파싱만 수행한다.

→ LLM이 "어떤 에이전트가 어떤 일에 적합한가"를 판단할 의미적 메타정보가 시스템 차원에서 제공되지 않는다.

## 목표

각 에이전트에 외부 공개용 짧은 설명(`description`)을 부여하고, 다른 에이전트의 LLM 컨텍스트와 사용자 UI(멘션 자동완성, 참여자 목록)에 일관되게 노출한다.

비목표(YAGNI):
- 의미 기반 자동 라우팅(설명 임베딩으로 적합한 에이전트 자동 선택) — 추후 별도 설계
- 풍부한 메타데이터 스키마(capabilities, examples, tags) — 단일 텍스트 필드로 시작
- 온디맨드 메타데이터 lookup 툴 — 인라인 로스터로 시작, 필요 시 하이브리드로 확장

## 설계 결정

### D1. 출처: 새 `description` 컬럼 (vs 기존 `agents_md` 재사용)

기존 `agents_md`는 "에이전트 자기 자신용 규칙/지시문"으로 톤이 다르다. 외부 공개용은 별도 필드로 분리해 용도 혼선을 막는다.

### D2. 필수성: nullable

기존 에이전트는 description 없이 운영 중. 점진 도입을 위해 nullable로 두고, 비면 기존 동작(이름만 노출)으로 폴백한다.

### D3. 노출 채널: 인라인 로스터 (vs 온디맨드 툴 vs 하이브리드)

채팅은 인터랙티브 매체라 라운드트립이 비싸다. 일반적인 룸 참여자 수(한 자릿수~십 단위)에서 한 줄 × 10명 ≈ 1.5K 토큰 수준이라 매 턴 부담이 작다. `Agent` 테이블이 이미 메타데이터 저장소 역할을 하므로, 추후 `capabilities` 같은 깊은 필드를 추가해 툴 기반 하이브리드로 확장하는 경로는 열려 있다.

### D4. 포맷: description 있을 때만 inline append

```
- <@user:{uuid}> {name} ({kind}) — {description}
```

비면 ` — …` 부분을 통째로 생략. 200자 cap, 줄바꿈은 공백으로 치환.

### D5. UI 범위: backend + frontend 입력 + frontend 표시

DB 컬럼만 추가하면 채울 방법이 없어 본 목적이 달성되지 않는다. 입력 UI(에이전트 설정 다이얼로그)와 표시 UI(MentionPopover, ParticipantListPopover) 모두 동일 PR에 포함한다.

## 변경 사항

### 1. 데이터 모델

**`packages/cluster/doorae/db/models.py`** — `Agent` 클래스에 컬럼 추가:

```python
description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

DB 레벨 길이 제한 없음. 어플리케이션 레벨(Pydantic)에서 200자 제한.

Alembic revision 1개 추가 (nullable 컬럼 → 다운타임 없음).

### 2. REST API

**`packages/cluster/doorae/api/v1/agents.py` 인근 Pydantic 스키마**:

- `AgentCreate.description: str | None = Field(default=None, max_length=200)`
- `AgentUpdate.description: str | None`
- `AgentResponse` / `AgentDetail`에 포함

권한 체크(admin 전용) 그대로. 핸들러는 단순 패스스루.

### 3. WebSocket 프로토콜

**`packages/cluster/doorae/ws/protocol.py`** — `ParticipantBrief.description: str | None = None` 추가.

**`packages/cluster/doorae/ws/handler.py — _build_participants_brief`** — Agent join 시 `description` 컬럼 함께 select하여 brief에 채움. 유저/게스트는 `None`.

WS 프로토콜 변화는 추가 필드뿐 → 비파괴적. 구버전 클라이언트는 무시.

### 4. 에이전트 런타임 로스터

**`packages/agent/doorae_agent/integrations/claude_code.py — _compose_participants_roster`**:

```python
desc = (p.description or "").strip().replace("\n", " ").replace("\r", " ")
desc_part = f" — {desc[:200]}" if desc else ""
line = f"- <@user:{p.id}> {p.name} ({p.kind}){desc_part}"
```

UTF-8 안전 트렁케이트가 필요하면 `textwrap.shorten` 검토.

### 5. 프론트엔드 입력

**`packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`** (및 생성 흐름):

- description 입력 필드 (`<Input>` 또는 `<Textarea rows={2}>`)
- 라벨: "소개"
- 헬퍼: "다른 에이전트와 사용자가 이 에이전트를 인식할 때 참고합니다 (200자)"
- `maxLength={200}`
- 폼 상태/저장 핸들러에 통합
- DESIGN.md 가이드(spacing, near-black text, typography) 준수

### 6. 프론트엔드 표시

**`MentionPopover.tsx`**: 자동완성 항목 이름 아래 보조 라인으로 description, `text-ellipsis` truncate. description 없으면 보조 라인 미렌더.

**`ParticipantListPopover.tsx`**: 각 참여자 행에 동일 패턴.

두 컴포넌트는 이미 `ParticipantBrief`를 소비하므로, 백엔드에서 새 필드만 채우면 자연스럽게 흘러 들어온다.

DESIGN.md의 보조 텍스트 톤(낮은 대비, 작은 사이즈) 적용.

## 데이터 흐름

```
[관리자가 AgentSettingsDialog에서 description 입력]
          ↓
[POST/PATCH /api/v1/agents]  → Pydantic 검증 → DB 저장
          ↓
[클라이언트 WS join]  → _build_participants_brief가 Agent 조인
          ↓
[WelcomeOut.participants[*].description]  ── 두 갈래로 분기 ──
          ├─ Frontend: MentionPopover, ParticipantListPopover에서 렌더
          └─ Agent runtime: _compose_participants_roster가 LLM 시스템 프롬프트에 inline append
```

## 테스트 전략

**Backend** (`uv run pytest packages/`)
- `Agent` 모델: description 저장/조회 라운드트립
- REST: `POST/PATCH /api/v1/agents` description 라운드트립
- `_build_participants_brief`: agent에는 description 포함, 유저/게스트는 None
- `_compose_participants_roster`: 빈 값 / 일반 / 200자 초과 (트렁케이트) / 개행 포함

**Frontend** (`cd packages/cluster/frontend && npm run build`)
- 타입체크/빌드 통과
- `AgentSettingsDialog.test.tsx` 확장 — description 입력 및 저장 검증
- MentionPopover/ParticipantListPopover에 기존 테스트가 있으면 description 렌더 케이스 추가

**수동 검증** (`make dev`)
- 에이전트 생성/수정에서 description 입력 → 저장
- 멘션 자동완성과 참여자 팝오버에서 description 노출 확인
- 다른 에이전트가 description을 인식하는지 정성 평가

## 마이그레이션 & 호환성

- Alembic revision 1개, nullable 컬럼 → 다운타임 없음
- 기존 에이전트는 description=NULL로 시작, 점진 도입
- WS 프로토콜 비파괴 변경 → 구버전 클라이언트 안전

## 변경 파일 (예상)

- `packages/cluster/doorae/db/models.py` — Agent 컬럼 추가
- `packages/cluster/alembic/versions/<new>.py` — 마이그레이션 신설
- `packages/cluster/doorae/api/v1/agents.py` (또는 인근 schemas) — Pydantic 필드
- `packages/cluster/doorae/ws/protocol.py` — `ParticipantBrief.description`
- `packages/cluster/doorae/ws/handler.py` — `_build_participants_brief` 조정
- `packages/agent/doorae_agent/integrations/claude_code.py` — `_compose_participants_roster`
- `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx` — 입력
- `packages/cluster/frontend/src/components/MentionPopover.tsx` — 표시
- `packages/cluster/frontend/src/components/ParticipantListPopover.tsx` — 표시
- 관련 테스트 파일들

## 향후 확장 경로

- description으로 만족 못 하는 풍부한 메타데이터(capabilities, examples)는 별도 필드/엔터티로 추가하고 `lookup_agent(id)` 툴로 노출하여 하이브리드 구조로 진화 (D3 결정 시 검토함)
- 의미 기반 자동 라우팅(임베딩 기반)은 description이 충분한 학습 신호가 된 뒤 별도 설계
