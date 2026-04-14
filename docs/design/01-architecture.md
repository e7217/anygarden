# 01. 아키텍처 — 단일 프로세스 + 임베디드 SQLite + WebSocket

> **한 줄 요약**: 단일 Python 프로세스 서버 + 임베디드 SQLite + WebSocket 단일 프로토콜 + 패키지 스크립트 CLI. `uvx doorae-server` 한 줄로 기동.

Plan A의 토폴로지를 **구현 관점**에서 구체화한다. 모든 박스는 실제 파이썬 모듈·실제 의존성·실제 명령어에 1:1 매핑된다.

---

## 1.1 시스템 토폴로지

이 시스템은 4종류의 **물리적으로 독립된 호스트**로 구성된다:

1. **서버 호스트** — `doorae-server` 프로세스. 채팅 메시지 라우팅 + 영속화 + 스케줄링.
2. **Machine (N대)** — 각각 별도 PC/VM/서버. `doorae-machine` 데몬이 상주하며, 서버의 명령에 따라 에이전트 subprocess를 spawn/kill한다. Machine은 서버와 **물리적으로 별개의 호스트**이며, 네트워크(WebSocket)로만 연결된다.
3. **유저 클라이언트** — 브라우저 또는 CLI. 서버에 직접 WebSocket 연결.
4. **외부 MCP 도구** — 각 에이전트 엔진이 자체적으로 사용하는 외부 서비스. 서버와 무관.

서버에는 두 개의 WebSocket 경로가 존재한다:
- `/ws/rooms/{id}` — **데이터 평면** (유저와 에이전트의 채팅 메시지)
- `/ws/machines/{id}` — **제어 평면** (Machine Daemon ↔ 서버 간 spawn/kill 명령)

```mermaid
graph TB
    subgraph Users["유저 호스트"]
        Browser["웹 브라우저<br/>new WebSocket(url)"]
        CLI_User["uvx doorae-client<br/>(TUI / 텍스트 UI)"]
    end

    subgraph ServerHost["서버 호스트 (별도 머신)"]
        direction TB
        APP["FastAPI app<br/>(doorae.app)"]
        WS_ROOM["ws.handler<br/>/ws/rooms/{id}<br/>(데이터 평면)"]
        WS_MACHINE["ws.machine_handler<br/>/ws/machines/{id}<br/>(제어 평면)"]
        REST["REST API<br/>/api/v1/*"]
        AUTH["auth (토큰 3종)<br/>JWT · AgentToken · MachineToken"]
        MANAGER["ws.manager<br/>ConnectionManager"]
        SCHED["scheduler/<br/>placement · lifecycle<br/>MachineBus"]
        ORCH["orchestration.rules"]
        METRIC["observability<br/>Prometheus · structlog"]
        DB[("SQLite / PostgreSQL")]

        APP --> WS_ROOM
        APP --> WS_MACHINE
        APP --> REST
        APP --> METRIC
        WS_ROOM --> AUTH
        WS_MACHINE --> AUTH
        REST --> AUTH
        WS_ROOM --> MANAGER
        WS_MACHINE --> SCHED
        SCHED --> DB
        MANAGER --> ORCH
        MANAGER --> DB
        REST --> DB
    end

    subgraph MachineA["Machine A — alice-laptop (별도 PC)"]
        MD_A["doorae-machine daemon"]
        A1["Agent: PM<br/>(claude-code)"]
        A2["Agent: TechLead<br/>(codex)"]
        MD_A -.->|spawn| A1
        MD_A -.->|spawn| A2
    end

    subgraph MachineB["Machine B — gpu-server-01 (별도 서버)"]
        MD_B["doorae-machine daemon"]
        B1["Agent: Coder<br/>(openhands)"]
        B2["Agent: Analyst<br/>(deep-agents)"]
        MD_B -.->|spawn| B1
        MD_B -.->|spawn| B2
    end

    subgraph MachineC["Machine C — Phase 2 (TS 에이전트)"]
        MD_C["doorae-machine daemon"]
        C1["Agent: Designer<br/>(codex-ts via npx)"]
        MD_C -.->|spawn| C1
    end

    subgraph Ext["외부 MCP 도구 (서버 무관)"]
        GH["GitHub MCP"]
        FS["Filesystem MCP"]
        JIRA["Jira MCP"]
    end

    %% 유저 → 서버 (데이터 평면)
    Browser -->|"WS /ws/rooms/{id}<br/>bearer.user_token"| WS_ROOM
    CLI_User -->|"WS /ws/rooms/{id}<br/>bearer.user_token"| WS_ROOM

    %% Machine Daemon → 서버 (제어 평면, 별도 호스트 간 네트워크)
    MD_A <-..->|"WS /ws/machines/A<br/>bearer.machine_token"| WS_MACHINE
    MD_B <-..->|"WS /ws/machines/B<br/>bearer.machine_token"| WS_MACHINE
    MD_C <-..->|"WS /ws/machines/C<br/>bearer.machine_token"| WS_MACHINE

    %% Agent → 서버 (데이터 평면, 독립 연결)
    A1 -->|"WS /ws/rooms/{id}<br/>bearer.agent_token"| WS_ROOM
    A2 -->|"WS /ws/rooms/{id}<br/>bearer.agent_token"| WS_ROOM
    B1 -->|"WS /ws/rooms/{id}<br/>bearer.agent_token"| WS_ROOM
    B2 -->|"WS /ws/rooms/{id}<br/>bearer.agent_token"| WS_ROOM
    C1 -->|"WS /ws/rooms/{id}<br/>bearer.agent_token"| WS_ROOM

    %% 에이전트 → 외부 MCP (서버와 무관)
    A1 -.->|MCP| GH
    B1 -.->|MCP| FS
    C1 -.->|MCP| JIRA

    style ServerHost fill:#2563eb,stroke:#1e40af,color:#fff
    style MachineA fill:#059669,stroke:#065f46,color:#fff
    style MachineB fill:#ea580c,stroke:#9a3412,color:#fff
    style MachineC fill:#7c3aed,stroke:#5b21b6,color:#fff
    style Ext fill:#be185d,stroke:#831843,color:#fff
    style Users fill:#f59e0b,stroke:#b45309,color:#fff
```

**핵심 구조**:

- **Machine은 서버와 별개의 호스트**다. Machine이 주체이고, Machine이 자기 안에서 에이전트를 spawn한다.
- **`doorae-machine` daemon이 Machine의 대표자**다. 서버에 자기 capability(어떤 엔진이 설치되어 있는지)를 보고하고, 서버의 `spawn_agent` 명령을 받아 로컬에서 `uvx doorae-agent` subprocess를 실행한다.
- **Agent subprocess는 서버와 독립 WebSocket**을 맺는다. Daemon의 제어 경로(`/ws/machines/{id}`)와 Agent의 데이터 경로(`/ws/rooms/{id}`)는 완전히 다른 연결이다.
- **서버 스케줄러**가 "어떤 Machine에 어떤 에이전트를 spawn할지" 결정한다. 사용자가 `POST /api/v1/agents`로 에이전트를 선언적으로 요청하면, 스케줄러가 적합한 Machine을 bin-pack으로 선택하여 해당 Daemon에 명령을 보낸다.
- **외부 MCP 도구**는 각 에이전트 엔진이 자체적으로 사용한다. 서버도 Daemon도 MCP를 모른다.

상세 프로토콜, 스케줄러 알고리즘, 실패 복구는 [10-machine-scheduler.md](10-machine-scheduler.md) 참조.

**관찰 포인트**:

1. **서버 프로세스는 1개**. 수평 확장 없음. SPOF지만 MVP 단계에서는 의도된 경계다.
2. **Machine은 N개**. 같은 머신에 여럿 올려도 되고, 여러 VPS에 분산해도 된다. 서버는 호스트 구분을 모른다.
3. **MCP 화살표는 점선**. 서버 박스를 관통하지 않는다는 점이 시각적으로 중요하다. MCP는 각 에이전트 엔진이 자체 처리한다.
4. **TS SDK는 Phase 2**. Phase 1에서는 Python SDK만 구현한다 (OpenHands/Deep Agents 필수 지원을 위해).

---

## 1.2 코드 레이아웃

Plan A의 "~710줄 서버 본체" 추정치를 이 구현 트리에 재매핑한다 (§1.3에서 CLI/모듈 분리를 반영해 **~960으로 확정**). 각 파일은 **실제로 존재할** 파일이며, 이름은 변경하지 않는다.

```
doorae-server/                    # 서버 레포지토리
├── pyproject.toml                # hatchling + [project.scripts] 엔트리포인트
├── README.md
├── LICENSE
├── doorae/                       # Python 패키지 루트 (import doorae)
│   ├── __init__.py               # __version__
│   ├── __main__.py               # python -m doorae 지원
│   ├── cli.py                    # doorae-server / doorae-client CLI 엔트리
│   ├── config.py                 # Pydantic Settings (~/.doorae/config.toml)
│   ├── app.py                    # FastAPI app factory, lifespan, 미들웨어
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py             # SQLAlchemy async engine (SQLite/PG)
│   │   ├── models.py             # 7개 엔티티 ORM (Project/Room/User/...)
│   │   ├── repository.py         # append_message 등 공용 쿼리
│   │   └── migrations/           # Alembic versions/
│   │       ├── env.py
│   │       └── versions/
│   │
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── jwt.py                # 유저 JWT (HS256, 24h)
│   │   ├── token.py              # 에이전트 API Token (argon2 해시)
│   │   └── dependencies.py       # FastAPI Dependency: require_member 등
│   │
│   ├── ws/
│   │   ├── __init__.py
│   │   ├── manager.py            # ConnectionManager (Room 구독 관리)
│   │   ├── handler.py            # /ws/rooms/{id} WebSocket 엔드포인트
│   │   └── protocol.py           # 메시지 프레임 Pydantic v2 모델
│   │
│   ├── rooms/
│   │   ├── __init__.py
│   │   ├── router.py             # REST API: GET/POST/DELETE /api/v1/rooms
│   │   └── service.py            # 비즈니스 로직 (sub-room 권한 상속)
│   │
│   ├── messages/
│   │   ├── __init__.py
│   │   ├── router.py             # REST API: 히스토리 페이지네이션
│   │   └── service.py            # append_message + seq 발급
│   │
│   ├── orchestration/
│   │   ├── __init__.py
│   │   └── rules.py              # 서버 최소 룰: 쿨다운, 멘션 라우팅
│   │
│   └── observability/
│       ├── __init__.py
│       ├── metrics.py            # prometheus_client Counter/Gauge/Histogram
│       └── logging.py            # structlog JSON formatter
│
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_ws_handler.py
│   ├── test_orchestration.py
│   └── test_e2e_chat.py          # 실 WebSocket 클라로 엔드투엔드
│
└── scripts/
    ├── dev.sh                    # uvicorn --reload + SQLite init
    └── build-binary.sh           # PyInstaller --onefile
```

**SDK 레포지토리는 분리**:

```
doorae-sdk/                       # 별도 PyPI 패키지
├── pyproject.toml
├── doorae_sdk/
│   ├── __init__.py
│   ├── client.py                 # ChatClient (WebSocket 래퍼 + 재연결)
│   ├── cli.py                    # doorae-agent CLI
│   ├── protocol/
│   │   ├── frames.py             # 서버 doorae/ws/protocol.py 와 동일한 Pydantic 모델
│   │   └── versioning.py
│   └── integrations/
│       ├── __init__.py
│       ├── base.py               # 공통 훅 인터페이스
│       ├── claude_code.py        # integrate_with_claude_code
│       ├── codex.py              # integrate_with_codex
│       ├── openhands.py          # integrate_with_openhands
│       ├── deep_agents.py        # integrate_with_deep_agents
│       └── openai.py             # integrate_with_openai (일반 LLM API)
└── tests/
```

**구조적 원칙**:

- 서버와 SDK는 **별도 레포지토리**로 관리한다. 같은 모노레포로 묶지 않는다.
  이유: 서버 릴리스와 SDK 릴리스 주기가 다르고, SDK는 여러 언어 버전이 생긴다.
- 두 레포는 **`protocol/frames.py` 파일을 문자 그대로 공유**한다 (복사본 1부씩).
  와이어 포맷이 깨지면 CI가 터지도록 `tests/test_protocol_compat.py`가 두 파일의 해시 비교를 한다.
- `doorae-server`는 `doorae-sdk`를 의존하지 않는다 (역방향 의존 없음).
- `doorae-sdk`도 `doorae-server`를 의존하지 않는다.

---

## 1.3 모듈별 LOC 예산

Plan A §3.2의 표를 이 구현 트리에 재매핑한다. Plan A의 "필수 합계 ~710" 대비 CLI/모듈 분리 비용을 반영하여 **필수 합계 ~960**으로 확정한다. 모듈명은 위 트리 기준이다.

| 모듈 | 기대 LOC | 계층 | 역할 |
|---|---:|---|---|
| `doorae/app.py` | 50 | 필수 | FastAPI 앱 팩토리, lifespan(startup/shutdown), CORS, 미들웨어 등록 |
| `doorae/config.py` | 30 | 필수 | Pydantic Settings (DB_URL, JWT_SECRET, CORS, LOG_LEVEL, DATA_DIR) |
| `doorae/db/engine.py` | 25 | 필수 | async engine + async session maker |
| `doorae/db/models.py` | 130 | 필수 | 7개 엔티티 + 제약조건 + 인덱스 |
| `doorae/db/repository.py` | 60 | 필수 | `append_message`, `replay_since_seq`, `list_rooms` |
| `doorae/auth/jwt.py` | 40 | 필수 | JWT 인코딩/디코딩 |
| `doorae/auth/token.py` | 45 | 필수 | API Token 발급·검증·argon2 해시 |
| `doorae/auth/dependencies.py` | 40 | 필수 | `require_user`, `require_room_member`, `require_admin` |
| `doorae/ws/manager.py` | 80 | 필수 | ConnectionManager (dict[room_id, set[WebSocket]]) |
| `doorae/ws/handler.py` | 90 | 필수 | /ws/rooms/{id} 핸들러, 프레임 파싱, heartbeat |
| `doorae/ws/protocol.py` | 50 | 필수 | Pydantic 프레임 (SendMessage, Message, JoinRoom, ...) |
| `doorae/rooms/router.py` | 70 | 필수 | REST: rooms CRUD + sub-room 생성 |
| `doorae/rooms/service.py` | 60 | 필수 | 권한 상속, parent_room 검증 |
| `doorae/messages/router.py` | 40 | 필수 | REST: 히스토리 페이지네이션 |
| `doorae/messages/service.py` | 50 | 필수 | `append_message(room, content)` + seq 발급 |
| `doorae/orchestration/rules.py` | 50 | 필수 | 쿨다운 토큰 버킷, 멘션 파싱 |
| `doorae/observability/metrics.py` | 30 | 필수 | 5개 Prometheus 지표 선언 |
| `doorae/observability/logging.py` | 20 | 필수 | structlog JSON 설정 |
| `doorae/cli.py` | 80 | 필수 | Typer/click CLI: server/client/admin |
| `doorae/db/migrations/` | 80 | Tier 1 부속 | Alembic env.py + 초기 마이그레이션 |
| **필수 합계** | **~960** | | |
| 테스트 (`tests/`) | ~500 | 부속 외 | pytest + httpx + websockets |

**합계가 ~960으로 늘어난 이유** (Plan A의 ~710 대비):

- `cli.py` ~80줄 추가 (Plan A에는 없었음 — uvx 엔트리포인트 대응)
- `auth/dependencies.py` ~40줄 분리 (Plan A의 `auth.py`에 포함되어 있던 것을 가시화)
- `db/repository.py` ~60줄 분리 (Plan A는 router.py에 흡수)
- `rooms/service.py` ~60줄 분리 (sub-room 권한 상속 로직 명시)

순증 ~250줄은 "CLI 대응 + 모듈 분리"에 대한 정직한 비용이다. 총합 ~960줄이 이 구현의 기준선(§1-§9 본체; Machine 스케줄링 계층은 §1.10.4와 §10에서 별도 합산)이다.

---

## 1.4 데이터 모델 (SQLAlchemy 2.0)

7개 공통 엔티티 ERD. Plan A §4.1과 동일한 구조이며, 여기서는 SQLAlchemy 2.0 타입 힌트 스타일로 한 번 더 명시한다.

```mermaid
erDiagram
    Project ||--o{ Room : contains
    Project ||--o{ Agent : owns
    Room ||--o{ Room : "parent_of"
    Room ||--o{ Participant : has
    Room ||--o{ Message : stores
    User ||--o{ Participant : "joins as"
    Agent ||--o{ Participant : "joins as"
    Machine ||--o{ Agent : hosts
    Participant ||--o{ Message : sends

    Project { uuid id PK; string name; timestamp created_at }
    Room { uuid id PK; uuid project_id FK; string name; uuid parent_room_id FK "nullable"; bool is_dm; bool archived; timestamp created_at }
    User { uuid id PK; string email UK; string display_name }
    Agent { uuid id PK; uuid project_id FK; string name; string engine; uuid machine_id FK; json metadata }
    Machine { uuid id PK; string hostname; string region }
    Participant { uuid id PK; uuid room_id FK; uuid subject_id; string subject_kind "user|agent"; string role "observer|member|admin" }
    Message { uuid id PK; uuid room_id FK; uuid participant_id FK; bigint seq; text content; json metadata; timestamp created_at }
```

**SQLAlchemy 2.0 모델 예시** (`doorae/db/models.py` 발췌):

```python
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, String, Text, Boolean, BigInteger, JSON, CheckConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.sqlite import BLOB  # UUID as BLOB in SQLite

class Base(DeclarativeBase):
    pass

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    parent_room_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rooms.id"), nullable=True
    )
    is_dm: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "parent_room_id IS NULL OR parent_room_id <> id",
            name="rooms_no_self_parent",
        ),
        Index("idx_rooms_parent", "parent_room_id"),
    )

class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rooms.id"), index=True)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"))
    seq: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str] = mapped_column(Text)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("uq_messages_room_seq", "room_id", "seq", unique=True),
    )
```

나머지 `User`, `Agent`, `Machine`, `Participant` 모델도 동일한 스타일로 `doorae/db/models.py`에 구현한다.

**`Participant.subject_kind` 다형성**: Plan A §4.2 결정 3과 동일. `(subject_id, subject_kind)` 쌍이며, FK 무결성은 앱 레이어에서 검증한다.

---

## 1.5 WebSocket 프레임 프로토콜

`doorae/ws/protocol.py`의 Pydantic v2 모델로 정의한다. JSON 텍스트 프레임만 사용 (바이너리 없음).

### 클라이언트 → 서버 (C2S)

| 프레임 `type` | 설명 | 예시 |
|---|---|---|
| `send_message` | Room에 메시지 게시 | `{"type":"send_message","room_id":"<uuid>","content":"hi"}` |
| `join_room` | 새 Room 구독 (이미 참여자여야 함) | `{"type":"join_room","room_id":"<uuid>","since_seq":4212}` |
| `leave_room` | Room 구독 해제 | `{"type":"leave_room","room_id":"<uuid>"}` |
| `create_sub_room` | 서브 채널 생성 + 자동 참여 | `{"type":"create_sub_room","parent_room_id":"...","participants":["agent_a","agent_b"],"name":"..."}` |
| `typing` | 타이핑 표시 브로드캐스트 | `{"type":"typing","room_id":"...","is_typing":true}` |

### 서버 → 클라이언트 (S2C)

| 프레임 `type` | 설명 |
|---|---|
| `welcome` | 연결 직후 서버가 보냄 (`protocol_version`, `server_id`) |
| `message` | 새 메시지 도착 (`seq` 포함) |
| `message_ack` | 내가 보낸 메시지의 확인 (`client_msg_id` → `seq`) |
| `participant_joined` | 누군가 Room에 들어옴 |
| `participant_left` | 누군가 Room을 떠남 |
| `room_created` | 서브 채널 생성 완료 (생성자에게) |
| `typing` | 다른 참여자가 타이핑 중 |
| `rate_limited` | 쿨다운 초과 (`retry_after` 포함) |
| `error` | 에러 (`code`, `message`) |
| `resync_required` | 재연결 시 놓친 메시지가 500개 초과 |

**Pydantic 모델 예시**:

```python
# doorae/ws/protocol.py
from __future__ import annotations
from typing import Literal, Annotated, Union
from uuid import UUID
from pydantic import BaseModel, Field

class SendMessageFrame(BaseModel):
    type: Literal["send_message"]
    room_id: UUID
    content: str = Field(min_length=1, max_length=10_000)
    client_msg_id: str | None = None  # idempotency
    metadata: dict = Field(default_factory=dict)

class JoinRoomFrame(BaseModel):
    type: Literal["join_room"]
    room_id: UUID
    since_seq: int = 0

class CreateSubRoomFrame(BaseModel):
    type: Literal["create_sub_room"]
    parent_room_id: UUID
    name: str
    participants: list[UUID]
    purpose: str | None = None

C2SFrame = Annotated[
    Union[SendMessageFrame, JoinRoomFrame, CreateSubRoomFrame, ...],
    Field(discriminator="type"),
]

class MessageFrame(BaseModel):
    type: Literal["message"] = "message"
    room_id: UUID
    seq: int
    sender_id: UUID
    sender_kind: Literal["user", "agent"]
    sender_name: str
    content: str
    metadata: dict = Field(default_factory=dict)
    created_at: str  # ISO 8601
```

Pydantic v2의 **discriminated union**으로 `type` 필드 기반 라우팅이 자동화된다. 타입 안정성이 서버 핸들러까지 그대로 전달된다.

---

## 1.6 배포 토폴로지 3종

### 토폴로지 1: 로컬 개발 (최빈)

```
┌─────────────────────────────────────────┐
│  단일 머신 (개발자 랩탑)                │
│                                         │
│  Terminal 1: uvx doorae-server          │
│  Terminal 2: uvx doorae-agent (PM)      │
│  Terminal 3: uvx doorae-agent (Coder)   │
│  Terminal 4: uvx doorae-client (유저)   │
│                                         │
│  파일: ~/.doorae/doorae.db (SQLite)     │
│       ~/.doorae/config.toml             │
│       ~/.doorae/agents/*.yaml           │
└─────────────────────────────────────────┘
```

특징: **Docker·docker-compose·K8s 모두 없음**. 5분 안에 에이전트 2개가 대화하는 장면을 시연할 수 있어야 한다.

### 토폴로지 2: 단일 VPS (프로덕션 소형)

```
┌──────────────────────────────────────────┐
│  VPS ($10/mo, 1 vCPU, 1GB RAM)           │
│                                          │
│  systemd --user                          │
│   └─ doorae-server.service               │
│       └─ uvx doorae-server (WAL SQLite)  │
│                                          │
│  nginx (또는 Caddy)                      │
│   └─ TLS 종료 → ws://127.0.0.1:8000      │
│                                          │
│  cron: daily backup (.backup + rsync)    │
└──────────────────────────────────────────┘

Machine은 별도 VPS 또는 개발자 머신에서 기동
```

### 토폴로지 3: 바이너리 배포 (비개발자 / Python 미설치)

```
┌──────────────────────────────────────────┐
│  임의 Linux / macOS (Python 없음)        │
│                                          │
│  /usr/local/bin/doorae-server            │
│   (PyInstaller onefile, 20-50MB)         │
│                                          │
│  $ doorae-server                         │
│  [INFO] listening on :8000               │
└──────────────────────────────────────────┘
```

자세한 구현은 [08-operations.md](08-operations.md)에서 다룬다.

---

## 1.7 의존성 목록

`pyproject.toml`의 `dependencies` 섹션. 목표는 **10개 이하, 모두 PyPI 상위권**이다.

| 패키지 | 버전 | 용도 |
|---|---|---|
| `fastapi` | `>=0.110,<0.120` | 웹 프레임워크 + WebSocket |
| `uvicorn[standard]` | `>=0.29` | ASGI 서버 (websockets 라이브러리 포함) |
| `sqlalchemy[asyncio]` | `>=2.0,<2.1` | ORM |
| `aiosqlite` | `>=0.19` | SQLite async 드라이버 |
| `pydantic` | `>=2.6,<3.0` | 데이터 검증 |
| `pydantic-settings` | `>=2.2` | config.toml → Settings 매핑 |
| `python-jose[cryptography]` | `>=3.3` | JWT |
| `argon2-cffi` | `>=23.1` | API Token 해시 |
| `structlog` | `>=24.1` | 구조화 로그 |
| `prometheus-client` | `>=0.20` | 메트릭 |
| `typer` | `>=0.12` | CLI (click 래퍼) |
| `alembic` | `>=1.13` | DB 마이그레이션 |

선택적 의존성 (extras):

```toml
[project.optional-dependencies]
postgres = ["asyncpg>=0.29"]
binary = ["pyinstaller>=6.3"]
tui = ["textual>=0.50"]  # doorae-client TUI
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "httpx>=0.27", "websockets>=12.0"]
```

**총 런타임 의존성 12개**. 모두 Python 서버 개발의 표준 스택이며, 버려질 위험이 낮다.

---

## 1.8 설정 파일 (`~/.doorae/config.toml`)

`doorae/config.py`가 읽는 기본 경로는 `~/.doorae/config.toml`이다. 없으면 기본값으로 동작한다.

```toml
# ~/.doorae/config.toml (예시)
[server]
host = "127.0.0.1"
port = 8000
log_level = "INFO"

[db]
# SQLite 기본. PG로 승격 시: "postgresql+asyncpg://user:pass@host/doorae"
url = "sqlite+aiosqlite:///~/.doorae/doorae.db"
pool_size = 5

[auth]
jwt_secret_file = "~/.doorae/jwt.secret"   # 파일이 없으면 첫 실행 시 자동 생성
jwt_expire_hours = 24
token_expire_days = 90

[orchestration]
cooldown_enabled = false
cooldown_per_room_per_sec = 3
mention_priority = true

[observability]
metrics_enabled = true
metrics_path = "/metrics"
log_format = "json"  # 또는 "console"
```

환경 변수로도 오버라이드 가능하다 (`DOORAE_SERVER_PORT=9000` 등, pydantic-settings 자동 매핑).

---

## 1.9 핵심 런타임 경로

시스템의 **hot path** 한 줄 요약:

```
브라우저 WS send
  → doorae/ws/handler.py: 프레임 파싱 (Pydantic)
  → doorae/auth/dependencies.py: Room 멤버 검증 (캐시)
  → doorae/orchestration/rules.py: 쿨다운 체크
  → doorae/messages/service.py: append_message + seq 발급 (트랜잭션)
  → doorae/ws/manager.py: Room 구독자 전원에 broadcast
  → 모든 구독자의 WebSocket.send_text(json)
```

이 경로의 목표 지연은 **서버 내부 p99 <50ms** (LLM 호출 제외). Plan A의 "단일 프로세스 수직 확장"으로 충분히 달성 가능하다. 자세한 성능 목표는 [07-error-recovery.md](07-error-recovery.md)에서 다룬다.

---

## 1.10 Machine 스케줄링 계층 (§10에서 상세)

이 문서의 §1.1~§1.9는 "채팅 서버가 어떻게 동작하는가"를 다룬다. 그러나 원래 요구사항("4대 엔티티: 프로젝트·머신·에이전트·유저")의 **Machine**을 1급 스케줄링 리소스로 다루는 계층이 별도로 필요하다. 이것이 **§10 Machine 스케줄링 계층**이다.

### 1.10.1 이 문서와의 관계

§1~§9의 내용은 그대로 유지되며, §10은 그 위에 **추가되는 계층**이다:

| 계층 | 담당 파일 | 역할 |
|---|---|---|
| 채팅 레이어 (이 문서) | `doorae/ws/handler.py` 등 | Room, Message, Participant, Subagent channel |
| **스케줄링 레이어 (§10)** | `doorae/scheduler/`, `doorae/ws/machine_handler.py` | Machine 등록, Daemon ↔ 서버 WS, Agent subprocess 생성/종료, bin-pack placement |

두 레이어는 **독립적**이다. Machine Daemon이 죽어도 채팅은 동작하고, 채팅 서버가 재시작되어도 Machine Daemon들은 재연결 후 상태 복구한다.

### 1.10.2 저장소 레이아웃 확장

§1.3에서 서버와 SDK 2개 저장소를 정의했다. §10은 **3번째 저장소**를 추가한다:

| 저장소 | import | 설명 |
|---|---|---|
| `doorae-server/` | `doorae` | 이 문서의 메인 서버 (변경 없음, `doorae/scheduler/`와 `doorae/ws/machine_handler.py` 추가) |
| `doorae-sdk/` | `doorae_sdk` | 에이전트 프로세스가 사용하는 클라이언트 SDK (변경 없음) |
| **`doorae-machine/`** (신규) | `doorae_machine` | **머신 호스트에 상주하는 Daemon. 엔진 감지, subprocess 관리** |

### 1.10.3 전체 토폴로지 (머신 계층 포함)

§1.1의 시스템 토폴로지 다이어그램은 이미 Machine 스케줄링 계층을 포함하고 있다. 핵심 구조를 재확인한다:

- **Machine Daemon과 Agent 프로세스는 서로 다른 WebSocket**으로 서버에 연결한다.
- Machine Daemon: `/ws/machines/{id}` — **제어 평면** (spawn/kill 명령, Machine Token 인증)
- Agent 프로세스: `/ws/rooms/{id}` — **데이터 평면** (채팅 메시지, Agent Token 인증)
- 유저: `/ws/rooms/{id}` — 데이터 평면 (User JWT 인증)
- 토큰은 3종으로 완전히 분리된다 (User JWT / Agent Token / Machine Token). 단, **Daemon 침해 시 자기가 spawn한 Agent의 토큰이 노출되는 구조적 한계**가 있다 — §10.12.4 참조.

### 1.10.4 전체 LOC 예산 갱신

§1.3의 LOC 표에 Machine 스케줄링 계층을 추가한다. §1-§9 본체의 **확정 기준선은 ~960**이고 (§1.3), 여기에 §10의 Machine 스케줄링 구현 범위(~370-790)를 더해 `doorae-server` 본체 전체 합계는 **~1,330-1,750** 범위에 들어온다. 범위의 폭은 "최소 필수 구현(§10.7 기준)과 여유 포함 상한(§10.13 기준)"의 차이다.

| 카테고리 | §1-§9 | §10 추가 | 합계 |
|---:|---:|---:|---:|
| `doorae-server` 본체 | ~960 | ~370-790 | ~1,330-1,750 |
| `doorae-sdk` | ~400 | — | ~400 |
| `doorae-machine` (신규 패키지) | — | ~410 | ~410 |

> 참고: Plan A 원본은 §1-§9 본체를 ~710으로 추정했으나, 본 구현에서는 CLI 엔트리포인트(cli.py ~80)와 모듈 분리(auth/dependencies, db/repository, rooms/service ~160)를 반영해 ~960으로 확정했다(§1.3). §10 범위의 하한 ~370은 10-machine-scheduler.md §10.7의 최소 스케줄러, 상한 ~790은 §10.13의 여유 포함 상한이다. 두 숫자의 차이는 "재시도/백오프/관측성 보강 여유"이며 실제 설계 범위는 동일하다.

§10 상세는 [10-machine-scheduler.md](10-machine-scheduler.md) 참조.
