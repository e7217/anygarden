# Week 1: doorae-server 뼈대

> **목표**: `uvx doorae-server`를 실행하면 5초 이내에 WebSocket 채팅이 동작하는 서버
> **산출물**: `doorae-server/` 저장소, 단위 테스트 40개, SQLite DB 자동 생성
> **정본 참조**: [01-architecture.md](../01-architecture.md) §1.2-§1.8, [05-security.md](../05-security.md) §5.1-§5.3, [08-operations.md](../08-operations.md) §8.2

---

## 1. 요약

Week 1은 **doorae-server 저장소를 처음부터 생성**하고, 다음 컴포넌트를 갖춘 뼈대를 만든다:
- FastAPI + uvicorn 앱
- SQLAlchemy async + SQLite + Alembic 마이그레이션
- 7개 엔티티 ORM 모델 (Project, Room, User, Agent, Machine, Participant, Message)
- JWT(유저) + API Token(에이전트) 인증 (Sec-WebSocket-Protocol subprotocol 헤더)
- WebSocket handler (`/ws/rooms/{id}`) + ConnectionManager
- 기본 CLI (`doorae-server` 명령)

Week 1 끝에 **2명이 WebSocket으로 연결하여 메시지를 주고받을 수 있어야** 한다. Room CRUD나 오케스트레이션은 Week 2.

---

## 2. 생성할 파일 (정확한 경로)

```
doorae-server/
├── pyproject.toml                          # §8.2.1 정본
├── README.md
├── LICENSE
├── doorae/
│   ├── __init__.py                         # __version__ = "0.1.0"
│   ├── __main__.py                         # python -m doorae 지원
│   ├── cli.py                              # [80 LOC] click CLI: server_main
│   ├── config.py                           # [30 LOC] Pydantic Settings
│   ├── app.py                              # [50 LOC] FastAPI factory + lifespan
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py                       # [25 LOC] async engine + session maker
│   │   ├── models.py                       # [130 LOC] 7개 엔티티 ORM
│   │   ├── repository.py                   # [60 LOC] append_message, replay_since_seq
│   │   └── migrations/
│   │       ├── env.py                      # Alembic async 설정
│   │       └── versions/
│   │           └── 001_initial.py          # 초기 스키마
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── jwt.py                          # [40 LOC] JWT HS256
│   │   ├── token.py                        # [45 LOC] Agent API Token + argon2
│   │   └── dependencies.py                 # [40 LOC] get_identity, require_room_member
│   ├── ws/
│   │   ├── __init__.py
│   │   ├── manager.py                      # [80 LOC] ConnectionManager
│   │   ├── handler.py                      # [90 LOC] /ws/rooms/{id} 핸들러
│   │   └── protocol.py                     # [50 LOC] Pydantic 프레임 모델
│   └── observability/
│       ├── __init__.py
│       ├── metrics.py                      # [30 LOC] Prometheus 5개 지표
│       └── logging.py                      # [20 LOC] structlog 설정
├── tests/
│   ├── conftest.py                         # DB fixture, test client
│   ├── test_auth.py                        # JWT/Token 발급·검증·만료
│   ├── test_ws_handler.py                  # WS 연결, 메시지 송수신, since_seq
│   ├── test_models.py                      # 엔티티 CRUD, 제약조건
│   └── test_config.py                      # 기본값, 환경변수 오버라이드
└── alembic.ini
```

**합계**: ~770 LOC (Week 1 범위, rooms/messages/orchestration 미포함)

---

## 3. 구현 단계 (순서대로)

### Phase 1A: 프로젝트 스캐폴딩 (Day 1 오전)

- [ ] `doorae-server/` 디렉토리 생성
- [ ] `pyproject.toml` 작성 (08-operations.md §8.2.1 정본 복사)
- [ ] `alembic.ini` 작성 (async SQLite 설정)
- [ ] `doorae/__init__.py`, `doorae/__main__.py` 생성
- [ ] `doorae/config.py` 작성 — Pydantic Settings:
  ```python
  class DooraeSettings(BaseSettings):
      host: str = "127.0.0.1"
      port: int = 8000
      db_url: str = f"sqlite+aiosqlite:///{Path.home()}/.doorae/doorae.db"
      jwt_secret: str = ""  # 첫 실행 시 자동 생성
      log_level: str = "INFO"
      model_config = SettingsConfigDict(env_prefix="DOORAE_")
  ```
- [ ] **검증**: `pip install -e .` 성공

### Phase 1B: DB 계층 (Day 1 오후)

- [ ] `doorae/db/engine.py` — `create_async_engine` + `async_sessionmaker`
- [ ] `doorae/db/models.py` — 7개 엔티티:
  - `Project`: id(UUID PK), name, description, created_at
  - `Room`: id, project_id(FK), name, parent_room_id(nullable FK self-ref), is_dm(bool), created_at
  - `User`: id, email, password_hash, is_admin, created_at
  - `Agent`: id, name, engine, placed_on_machine_id(nullable FK), desired_state, actual_state, profile_yaml, created_at
  - `Machine`: id, name, hostname, owner_user_id(FK), status, daemon_last_seen_at, cpu_cores, memory_gb, max_agents, labels(JSON)
  - `Participant`: id, room_id(FK), user_id(nullable FK), agent_id(nullable FK), role(observer/member/admin), joined_at
  - `Message`: id, room_id(FK), participant_id(FK), content(Text), extra_metadata(JSON), seq(BigInteger, room별 unique), created_at
- [ ] `doorae/db/repository.py` — `append_message()` with seq 발급, `replay_since_seq()`
- [ ] `doorae/db/migrations/env.py` — async Alembic 설정
- [ ] `doorae/db/migrations/versions/001_initial.py` — 자동 생성 (`alembic revision --autogenerate`)
- [ ] **검증**: `pytest tests/test_models.py` — 엔티티 CRUD + 제약조건 (10개 테스트)

### Phase 1C: 인증 계층 (Day 2 오전)

- [ ] `doorae/auth/jwt.py`:
  - `create_user_token(user_id, email, is_admin) -> str`
  - `verify_user_token(token) -> UserClaims` (만료 시 InvalidToken)
  - `class InvalidToken(Exception)` 정의
- [ ] `doorae/auth/token.py`:
  - `generate_token() -> str` (secrets.token_urlsafe(48))
  - `hash_token(plaintext) -> str` (argon2)
  - `verify_token_hash(plaintext, hashed) -> bool`
  - `resolve_agent_token(db, plaintext) -> AgentToken | None`
- [ ] `doorae/auth/dependencies.py`:
  - `get_identity(db, authorization_header, sec_websocket_protocol) -> Identity`
    - HTTP: `Authorization: Bearer ...` 헤더
    - WS: `Sec-WebSocket-Protocol: doorae.v1, bearer.<token>` — §5.7.1 준수
    - **쿼리 파라미터 `?token=...` 금지**
  - `require_room_member(room_id, identity, db) -> Participant`
- [ ] **검증**: `pytest tests/test_auth.py` — 발급/검증/만료/거부 (10개 테스트)

### Phase 1D: WebSocket 채팅 (Day 2 오후 ~ Day 3)

- [ ] `doorae/ws/protocol.py` — Pydantic v2 프레임 모델:
  ```python
  class IncomingFrame(BaseModel):
      type: Literal["send", "typing", "create_room", "join_room"]
      # ... 타입별 필드
  class OutgoingFrame(BaseModel):
      type: Literal["message", "room_created", "join_room", "error"]
      # ... 타입별 필드
  ```
- [ ] `doorae/ws/manager.py` — ConnectionManager:
  - `subscribe(room_id, participant_id, ws)` / `unsubscribe()`
  - `broadcast(room_id, frame)` — Room의 모든 구독자에게 전송
  - `send_to(participant_id, frame)` — 특정 참여자에게
- [ ] `doorae/ws/handler.py`:
  - `@router.websocket("/ws/rooms/{room_id}")`
  - `Sec-WebSocket-Protocol` subprotocol 인증 → `websocket.accept(subprotocol="doorae.v1")`
  - `since_seq` 파라미터로 재연결 복구 (§7.2)
  - 메시지 수신 → `append_message()` → `broadcast()`
- [ ] **검증**: `pytest tests/test_ws_handler.py` — 연결/인증/메시지 송수신/재연결 (15개 테스트)

### Phase 1E: 앱 조립 + CLI (Day 3 ~ Day 4)

- [ ] `doorae/app.py` — FastAPI factory:
  ```python
  def create_app(config: DooraeSettings) -> FastAPI:
      app = FastAPI(title="Doorae", lifespan=lifespan)
      app.include_router(ws_router)
      app.add_middleware(...)
      return app
  ```
  - lifespan: DB engine 생성 + migration 자동 적용 + JWT secret 자동 생성
- [ ] `doorae/observability/metrics.py` — 5개 Prometheus 지표
- [ ] `doorae/observability/logging.py` — structlog 설정
- [ ] `doorae/cli.py`:
  - `@click.group(invoke_without_command=True)` → `main()`
  - `--host`, `--port`, `--db`, `--config`, `--log-level` 옵션
  - 첫 실행 시 `~/.doorae/` 자동 생성 + JWT secret 자동 발급
  - `doorae-server init`, `doorae-server migrate` 서브커맨드
- [ ] **검증**: `pytest tests/test_config.py` + 수동 `uvx doorae-server` 실행

### Phase 1F: 통합 검증 (Day 5)

- [ ] 수동 E2E 테스트:
  1. `uvx doorae-server` 실행
  2. `websocat` 또는 Python 스크립트로 WebSocket 접속 (subprotocol 인증)
  3. 메시지 전송 → 다른 연결에서 수신 확인
  4. 연결 끊기 → 재연결 시 `since_seq`로 놓친 메시지 수신
- [ ] `ruff check doorae tests` — 린트 통과
- [ ] `mypy doorae` — 타입 체크 통과
- [ ] 테스트 커버리지 목표 80%

---

## 4. 테스트 전략

| 범주 | 파일 | 테스트 수 | 핵심 시나리오 |
|------|------|---------|------------|
| 단위 | `test_auth.py` | 10 | JWT 발급/검증/만료, Token 발급/해시/검증, Identity 파싱 |
| 단위 | `test_models.py` | 10 | 엔티티 CRUD, FK 제약, parent_room_id self-ref, seq unique |
| 단위 | `test_config.py` | 5 | 기본값, 환경변수 오버라이드, JWT secret 자동 생성 |
| 통합 | `test_ws_handler.py` | 15 | WS 연결, subprotocol 인증, 메시지 송수신, broadcast, since_seq 복구, 인증 실패 거부 |
| **합계** | | **40** | |

---

## 5. 리스크 및 고려사항

| 리스크 | 영향 | 대응 |
|--------|------|------|
| SQLAlchemy async + SQLite의 `with_for_update()` no-op | seq 발급 시 동시성 문제 가능 | 단일 프로세스에서는 WAL writer 직렬화로 충분. §7.2.3 참조 |
| Alembic async 설정 복잡도 | 초기 설정에 시간 소요 | aiosqlite + `run_async()` 패턴 검증됨. alembic docs 참고 |
| `Sec-WebSocket-Protocol` subprotocol 인증이 일부 WS 클라이언트에서 지원 안 됨 | 테스트 도구 제약 | `websocat --protocol` 또는 Python `websockets` 라이브러리 사용 |
| click vs typer 선택 | 01의 §1.7에 typer 언급, 08에 click | click으로 확정 (08-operations.md 정본). 01의 typer 참조는 레거시 |

---

## 6. 완료 기준 (Definition of Done)

- [ ] `uvx doorae-server`가 5초 이내에 `[INFO] Doorae server listening on http://127.0.0.1:8000` 출력
- [ ] 첫 실행 시 `~/.doorae/doorae.db` 자동 생성 + migration 적용
- [ ] `~/.doorae/config.toml` 자동 생성 + JWT secret 자동 발급
- [ ] WebSocket `/ws/rooms/{room_id}` 접속 + 메시지 송수신 동작
- [ ] `Sec-WebSocket-Protocol: doorae.v1, bearer.<token>` 인증 동작
- [ ] 쿼리 파라미터 `?token=...` 거부
- [ ] `since_seq` 재연결 복구 동작
- [ ] `pytest` 40개 테스트 통과
- [ ] `ruff check` + `mypy` 에러 0
- [ ] `/metrics` 엔드포인트에서 Prometheus 지표 응답

---

## 7. 참고

- [01-architecture.md](../01-architecture.md) §1.2 코드 레이아웃, §1.3 LOC 표
- [05-security.md](../05-security.md) §5.1-§5.3 인증/권한, §5.7.1 WebSocket subprotocol 인증
- [07-error-recovery.md](../07-error-recovery.md) §7.2 Last-Seq 재연결
- [08-operations.md](../08-operations.md) §8.2 pyproject.toml, §8.2.2 CLI 스펙
