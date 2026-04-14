# Week 2: 서버 완성 + Python SDK + 첫 엔진 통합

> **목표**: Room CRUD + 오케스트레이션 + doorae-sdk ChatClient + Claude Code/OpenAI 엔진 통합
> **산출물**: doorae-server 완성, doorae-sdk/ 저장소 생성, 통합 테스트 20개
> **정본 참조**: [03-agent-hierarchy.md](../03-agent-hierarchy.md), [04-orchestration.md](../04-orchestration.md), [08-operations.md](../08-operations.md) §8.3

---

## 1. 요약

Week 1의 뼈대 위에 다음을 추가한다:
- **Room CRUD REST API** + sub-room 생성 (채널 기반 서브에이전트)
- **메시지 히스토리 REST API** (페이지네이션)
- **오케스트레이션 최소 룰** (쿨다운, 멘션 라우팅)
- **doorae-sdk** 저장소 생성 + ChatClient (WebSocket 래퍼 + 재연결)
- **첫 엔진 통합**: Claude Code SDK + 일반 OpenAI API

Week 2 끝에 **에이전트가 ChatClient로 Room에 참여하고 메시지를 주고받을 수 있어야** 한다.

---

## 2. doorae-server 추가 파일

```
doorae-server/doorae/
├── rooms/
│   ├── router.py                    # [70 LOC] REST: GET/POST/DELETE /api/v1/rooms
│   └── service.py                   # [60 LOC] sub-room 권한 상속, parent_room 검증
├── messages/
│   ├── router.py                    # [40 LOC] REST: 히스토리 페이지네이션
│   └── service.py                   # [50 LOC] append_message + seq (Week 1의 repository 연동)
└── orchestration/
    └── rules.py                     # [50 LOC] 쿨다운 토큰 버킷, 멘션 파싱
```

**추가 LOC**: ~270. 서버 합계 ~770 + ~270 = **~1,040** (목표 ~960의 +8%, 허용 범위).

## 3. doorae-sdk 새 저장소

```
doorae-sdk/
├── pyproject.toml                   # §8.3.1 정본
├── README.md
├── doorae_sdk/
│   ├── __init__.py
│   ├── cli.py                       # [80 LOC] doorae-agent + doorae-client CLI
│   ├── client.py                    # [120 LOC] ChatClient (WebSocket + 재연결)
│   ├── protocol/
│   │   ├── __init__.py
│   │   ├── frames.py                # [50 LOC] 서버 doorae/ws/protocol.py 복사본
│   │   └── versioning.py            # [20 LOC]
│   ├── auth/
│   │   └── token.py                 # [30 LOC] 환경변수/파일에서 토큰 로드
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── base.py                  # [40 LOC] EngineAdapter ABC
│   │   ├── claude_code.py           # [50 LOC] integrate_with_claude_code
│   │   └── openai.py               # [50 LOC] integrate_with_openai
│   └── profile/
│       ├── __init__.py
│       ├── loader.py                # [30 LOC] ~/.doorae/agents/*.yaml
│       └── schema.py                # [20 LOC]
└── tests/
    ├── conftest.py
    ├── test_client.py               # ChatClient 단위 테스트
    ├── test_protocol_compat.py      # 서버 frames.py 해시 비교
    └── test_integrations/
        ├── test_claude_code.py
        └── test_openai.py
```

**SDK LOC**: ~490 (목표 ~400의 +22%, 엔진 2종 + CLI 포함).

---

## 4. 구현 단계

### Phase 2A: Room CRUD + Sub-room (Day 1)

- [ ] `doorae/rooms/router.py`:
  - `POST /api/v1/rooms` — 프로젝트 내 Room 생성 (parent_room_id 선택)
  - `GET /api/v1/rooms?project_id=...` — Room 목록
  - `GET /api/v1/rooms/{id}` — Room 상세 (참여자 포함)
  - `POST /api/v1/rooms/{id}/participants` — 참여자 추가
  - `DELETE /api/v1/rooms/{id}` — Room 삭제 (cascade: 자식 Room 아카이브)
- [ ] `doorae/rooms/service.py`:
  - `create_sub_room(parent_room_id, participants, is_dm, creator_participant_id)`
  - 권한 상속: 부모 Room의 member 이상만 자식 생성 가능 (§3.8)
  - self-reference 방지 CHECK (§3 §3.3)
- [ ] **검증**: `pytest tests/test_rooms.py` — Room CRUD + sub-room 6개 테스트

### Phase 2B: 메시지 히스토리 + 오케스트레이션 (Day 2)

- [ ] `doorae/messages/router.py`:
  - `GET /api/v1/rooms/{id}/messages?since_seq=N&limit=50` — 페이지네이션
- [ ] `doorae/messages/service.py`:
  - Week 1의 `repository.py` 기능을 정리하여 service 레이어로 이동
  - `append_message()` + seq 발급 (변경 없음, 인터페이스만 정리)
- [ ] `doorae/orchestration/rules.py`:
  - 쿨다운 토큰 버킷 (§4.2, `settings.orchestration.cooldown_ms`)
  - 멘션 파싱 (`@AgentName` → 해당 참여자에게 priority 알림) (§4.3)
  - typing 상태 브로드캐스트 (§4.4)
- [ ] `doorae/ws/handler.py` 확장: 오케스트레이션 룰 적용
- [ ] **검증**: 오케스트레이션 테스트 5개 (쿨다운, 멘션, typing)

### Phase 2C: doorae-sdk 스캐폴딩 + ChatClient (Day 3)

- [ ] `doorae-sdk/` 저장소 생성
- [ ] `pyproject.toml` 작성 (§8.3.1 정본)
- [ ] `doorae_sdk/protocol/frames.py` — 서버 `doorae/ws/protocol.py`에서 복사
- [ ] `doorae_sdk/auth/token.py` — 환경변수 `DOORAE_TOKEN` 로드
- [ ] `doorae_sdk/client.py` — ChatClient:
  - `join_room(room_id)` — WebSocket 연결 + subprotocol 인증
  - `send(room_id, content, metadata)` — 메시지 전송
  - `create_sub_room(parent_room_id, participants, purpose)` — 서브채널 생성
  - `on_message(handler)` — 수신 콜백 등록 (데코레이터)
  - `on_join_room(handler)` — Room 참여 알림
  - `_room_loop()` — 재연결 루프 (지수 백오프, since_seq 복구)
- [ ] **검증**: `pytest tests/test_client.py` — 연결/재연결/콜백 5개 테스트

### Phase 2D: 엔진 통합 — Claude Code + OpenAI (Day 4)

- [ ] `doorae_sdk/integrations/base.py` — EngineAdapter ABC:
  ```python
  class EngineAdapter(ABC):
      @abstractmethod
      async def on_message(self, msg: MessageFrame) -> None: ...
      @abstractmethod
      async def start(self) -> None: ...
  ```
- [ ] `doorae_sdk/integrations/claude_code.py`:
  - `integrate_with_claude_code(client, agent)` → before_turn/after_response hook
  - 수신 메시지 → `agent.inject_user_message(f"[{sender}] {content}")`
  - 에이전트 응답 → `client.send(room_id, response.text)`
  - 상태: `conceptual` (실제 claude-agent-sdk 0.3.x API 미검증)
- [ ] `doorae_sdk/integrations/openai.py`:
  - `integrate_with_openai(client, openai_client, model)` → 직접 API 호출
  - 수신 메시지 → conversation append → `openai.chat.completions.create()`
  - 응답 → `client.send()`
  - 상태: `verified` (openai>=1.30 API 안정)
- [ ] **검증**: mock 기반 통합 테스트 4개

### Phase 2E: doorae-agent CLI + 프로필 로더 (Day 5)

- [ ] `doorae_sdk/profile/loader.py` — `~/.doorae/agents/*.yaml` 로더
- [ ] `doorae_sdk/profile/schema.py` — Pydantic 프로필 스키마 (name, engine, system_prompt, rooms, mcp_servers)
- [ ] `doorae_sdk/cli.py`:
  - `doorae-agent --engine claude-code --name PM --server ws://... --token $TOK`
  - `doorae-client --server ws://... --user me --room sprint-42`
  - 엔진 lazy import (§8.3.5)
- [ ] `tests/test_protocol_compat.py` — 서버의 `protocol.py`와 SDK의 `frames.py` 해시 비교
- [ ] **검증**: 수동 E2E — 서버 + OpenAI 에이전트 + 유저 CLI로 3자 대화

---

## 5. 테스트 전략

| 범주 | 위치 | 수 | 시나리오 |
|------|------|---|---------|
| 단위 (서버) | `test_rooms.py` | 6 | Room CRUD, sub-room, 권한 상속 |
| 단위 (서버) | `test_orchestration.py` | 5 | 쿨다운, 멘션, typing |
| 단위 (SDK) | `test_client.py` | 5 | 연결, 재연결, since_seq, 콜백 |
| 통합 (SDK) | `test_integrations/` | 4 | claude_code mock, openai mock |
| **합계** | | **20** | |

---

## 6. 완료 기준

- [ ] `POST /api/v1/rooms` + `GET /api/v1/rooms` 동작
- [ ] sub-room 생성 (`parent_room_id`) 동작
- [ ] 메시지 히스토리 페이지네이션 동작
- [ ] `uvx doorae-agent --engine openai --name Host` 실행 시 채팅 참여 동작
- [ ] `uvx doorae-client --user me --room test` 실행 시 메시지 송수신 동작
- [ ] 쿨다운 + 멘션 라우팅 기본 동작
- [ ] `tests/test_protocol_compat.py` 통과 (frames.py 해시 일치)
- [ ] 서버 + SDK 합계 60개 테스트 통과 (Week 1 40개 + Week 2 20개)

---

## 7. 참고

- [03-agent-hierarchy.md](../03-agent-hierarchy.md) — 채널 기반 서브에이전트, SQLAlchemy 모델
- [04-orchestration.md](../04-orchestration.md) — Host 에이전트 패턴, 서버 최소 룰
- [08-operations.md](../08-operations.md) §8.3 — SDK pyproject.toml, ChatClient API, CLI 스펙
- [06-mcp-integration.md](../06-mcp-integration.md) — MCP는 외부 도구 영역 (SDK가 관여 안 함)
