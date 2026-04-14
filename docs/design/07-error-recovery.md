# 07 · 에러 복구 및 성능 목표

> 서버는 얇은 메시징 허브이므로 에러 복구 책임의 대부분은 에이전트 엔진이나 네트워크 계층에 분리된다. 서버가 책임지는 것은 **메시지 영속화와 Last-Seq 기반 재연결 복구** 두 가지뿐이다.

## 7.1 에러 복구의 책임 분리

이 구현의 경량 철학상, 서버는 "모든 것을 복구"하려 하지 않는다. 대신 각 장애 카테고리의 책임 주체를 명확히 한다.

| 장애 유형 | 서버 책임 | 에이전트 엔진 책임 | 네트워크/운영 책임 |
|---|---|---|---|
| **LLM API 실패** | 없음 | 재시도, 폴백 모델, 세션 압축 | — |
| **MCP 도구 실패** | 없음 | 자체 retry, 에러 메시지로 변환 후 send | — |
| **서브에이전트 크래시** | 자식 Room 유지 | 부모 엔진이 재시도 or 에스컬레이션 | systemd `Restart=on-failure` |
| **네트워크 단절 (WS)** | Last-Seq 기반 재전송 | 지수 백오프 재연결 | — |
| **서버 재시작** | SQLite WAL로 데이터 유실 없음 | 재연결 후 `?since_seq` | — |
| **SQLite 손상** | 백업으로 복구 | — | cron 백업 스크립트 |
| **디스크 풀** | 에러 응답 + 메트릭 | 재시도 큐 | 알림 + 용량 확보 |
| **에이전트 프로세스 크래시** | 참여자 상태 `disconnected` 마킹 | supervisor 재시작 | systemd / supervisord |

**핵심 원칙**: 서버는 "메시지를 잃지 않는다"만 보장하면 된다. 나머지는 엔진과 운영 계층의 몫이다.

## 7.2 Last-Seq 기반 재연결 (서버 책임의 핵심)

### 7.2.1 개념

클라이언트(유저 or 에이전트)가 연결을 잃었다가 재접속할 때 놓친 메시지를 빠짐없이 복구하는 메커니즘. `messages.seq` 컬럼이 단조 증가 정수이며, 재연결 시 `?since_seq=N` 쿼리로 N 이후 메시지를 받는다.

```
시간 ────────────────────────────────→
        │                           │
        │     네트워크 단절 5초      │
        ▼                           ▼
    seq=100     seq=101  102  103   재연결
    (정상 수신)  (놓침)            GET /ws/rooms/X?since_seq=100
                                    → 서버가 101, 102, 103 재전송
```

### 7.2.2 SQLAlchemy 모델

```python
# doorae/db/models.py
class Message(Base):
    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    room_id: Mapped[UUID] = mapped_column(ForeignKey("rooms.id"), index=True)
    participant_id: Mapped[UUID] = mapped_column(ForeignKey("participants.id"))
    content: Mapped[str] = mapped_column(Text)
    extra_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # 재연결 복구의 핵심: Room별 단조 증가 seq
    seq: Mapped[int] = mapped_column(BigInteger, index=True)

    __table_args__ = (
        # (room_id, seq)는 유일 — Room 단위로 seq가 연속 증가
        UniqueConstraint("room_id", "seq", name="uq_messages_room_seq"),
        # (room_id, seq) 복합 인덱스로 재연결 쿼리 O(log n)
        Index("ix_messages_room_seq", "room_id", "seq"),
    )
```

### 7.2.3 seq 발급 전략

**SQLite (단일 프로세스, 기본)**:
```python
# doorae/messages/service.py
async def create_message(
    self,
    room_id: UUID,
    participant_id: UUID,
    content: str,
    metadata: dict | None = None,
) -> Message:
    async with self.db.begin():
        # Room별 MAX(seq) + 1 (같은 트랜잭션 내에서 원자적)
        result = await self.db.execute(
            select(func.coalesce(func.max(Message.seq), 0) + 1)
            .where(Message.room_id == room_id)
            .with_for_update()  # SQLite에선 no-op, PG에선 행 락
        )
        next_seq = result.scalar_one()

        msg = Message(
            room_id=room_id,
            participant_id=participant_id,
            content=content,
            extra_metadata=metadata or {},
            seq=next_seq,
        )
        self.db.add(msg)
        await self.db.flush()
        return msg
```

SQLite는 WAL 모드에서 단일 writer이므로 자연스럽게 직렬화된다. 별도 락이 필요 없다.

**PostgreSQL (수평 확장 시)**:
advisory lock으로 같은 Room의 동시 INSERT를 직렬화:
```python
# doorae/messages/service.py (PG 분기)
async def create_message_pg(self, room_id: UUID, ...) -> Message:
    async with self.db.begin():
        # Room ID를 hash해서 advisory lock 키로 사용
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:rid))"),
            {"rid": str(room_id)},
        )
        # 이후는 SQLite와 동일
        ...
```

### 7.2.4 재연결 프로토콜

**클라이언트 측 흐름**:

```python
# doorae_sdk/client.py
class ChatClient:
    def __init__(self, server_url: str, token: str):
        self.server_url = server_url
        self.token = token
        self.last_seq: dict[UUID, int] = {}  # room_id -> last received seq
        self.ws: WebSocketClientProtocol | None = None

    async def connect_room(self, room_id: UUID) -> None:
        last = self.last_seq.get(room_id, 0)
        # 토큰은 `Sec-WebSocket-Protocol` subprotocol 헤더로 전달한다.
        # 쿼리 파라미터는 access log/프록시 로그/브라우저 히스토리에 노출되므로 금지.
        # `since_seq`는 민감 정보가 아니므로 쿼리로 유지.
        url = f"{self.server_url}/ws/rooms/{room_id}?since_seq={last}"

        while True:  # 재연결 루프
            try:
                async with websockets.connect(
                    url,
                    subprotocols=["doorae.v1", f"bearer.{self.token}"],
                ) as ws:
                    self.ws = ws
                    async for frame in ws:
                        msg = parse_frame(frame)
                        if msg.type == "message":
                            self.last_seq[room_id] = msg.seq
                            await self.on_message(msg)
                        elif msg.type == "ack":
                            ...
            except websockets.ConnectionClosed:
                # 지수 백오프 재연결
                await asyncio.sleep(min(2 ** self.retry_count, 30))
                self.retry_count += 1
```

**서버 측 흐름**:

```python
# doorae/ws/handler.py
@router.websocket("/ws/rooms/{room_id}")
async def room_ws(
    websocket: WebSocket,
    room_id: UUID,
    since_seq: int = 0,
    # 토큰은 반드시 Sec-WebSocket-Protocol 헤더로만 받는다 (§5.7.1 참조).
    # 쿼리 파라미터 `token`은 사용하지 않는다.
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db),
    manager: ConnectionManager = Depends(get_ws_manager),
):
    # Room 멤버십 검증
    participant = await resolve_room_participant(identity, room_id, db)
    if participant is None:
        await websocket.close(code=1008, reason="not a room member")
        return

    # Subprotocol 협상: 클라이언트가 보낸 "doorae.v1, bearer.<token>" 중
    # "doorae.v1"을 선택하여 반환한다.
    await websocket.accept(subprotocol="doorae.v1")

    # 1) 재연결 복구 (since_seq > 0인 경우)
    if since_seq > 0:
        missed = await db.execute(
            select(Message)
            .where(Message.room_id == room_id, Message.seq > since_seq)
            .order_by(Message.seq)
            .limit(500)  # 일괄 전송 상한
        )
        for msg in missed.scalars():
            await websocket.send_json(to_frame(msg))

    # 2) 실시간 구독 시작
    await manager.subscribe(room_id, participant.id, websocket)
    try:
        async for frame in websocket.iter_json():
            await handle_frame(frame, participant, room_id, db, manager)
    except WebSocketDisconnect:
        await manager.unsubscribe(room_id, participant.id)
```

### 7.2.5 복구 창과 한계

| 항목 | 값 |
|---|---|
| 재연결 시 일괄 전송 상한 | **500건** (한 번에 더 많으면 여러 번 요청) |
| 과거 메시지 보관 기간 | **무기한** (디스크가 허용하는 한) |
| since_seq=0인 경우 | "Room 전체 히스토리 요청" → 상한 500건 반환 |
| Room별 최대 seq | 2^63 (BigInteger) |

500건 상한은 단일 TCP 프레임 내 블로킹을 방지하기 위함이다. 500건 이상을 원하는 클라이언트는 재귀적으로 `since_seq`를 올리며 폴링한다.

## 7.3 장애별 복구 시나리오

### 7.3.1 서버 재시작

**서버 측**:
- SQLite WAL 모드 → 트랜잭션 경계의 데이터는 모두 보존
- uvicorn이 재시작되면 WebSocket 연결은 모두 끊어짐
- 재시작 후 FastAPI app이 DB에서 다시 상태 로드 (stateless 서버)

**클라이언트 측**:
- WebSocket 끊김 감지 (ConnectionClosed)
- 지수 백오프 재연결 (1s → 2s → 4s → ... → 30s 상한)
- 재연결 성공 시 `?since_seq=N` 자동 첨부
- 사용자는 몇 초의 "연결 복구 중" 표시만 보고, 메시지 유실 없음

### 7.3.2 네트워크 파티션

Machine와 서버 사이의 네트워크가 일시 단절:

1. 에이전트 측: WebSocket 끊김 감지 → 메시지 큐에 로컬 버퍼링 (선택)
2. 에이전트 측: 재연결 루프 진입
3. 재연결 성공 시: `?since_seq=N`으로 놓친 메시지 수신
4. 로컬 버퍼에 있던 송신 메시지를 재전송

**중복 방지**: 송신 측에서 client-side `message_id`(UUID)를 생성하여 포함. 서버가 동일 `message_id`를 본 적 있으면 무시.

```python
# 에이전트 측 로컬 버퍼
class OutboundBuffer:
    def __init__(self):
        self.pending: list[dict] = []  # 아직 ACK 못 받은 메시지들

    async def send(self, ws, content: str):
        msg_id = str(uuid.uuid4())
        frame = {"type": "send", "message_id": msg_id, "content": content}
        self.pending.append(frame)
        try:
            await ws.send_json(frame)
        except ConnectionClosed:
            pass  # 재연결 시 pending 재전송

    def ack(self, msg_id: str):
        self.pending = [m for m in self.pending if m["message_id"] != msg_id]
```

### 7.3.3 LLM API 실패

서버는 관여하지 않음. 에이전트 엔진이 자체 처리.

| 엔진 | LLM 실패 시 동작 |
|---|---|
| Claude Code SDK | 자동 재시도 3회 → 실패 시 Turn 종료 + 에러 메시지 |
| Codex | Turn 내 재시도 → 에러 시 사용자에게 알림 |
| OpenHands | LLM provider fallback (설정된 경우) |
| Deep Agents | LangChain `RunnableRetry` 래핑 |

에이전트 SDK의 역할은 **LLM 에러를 채팅 메시지로 변환**하는 것뿐:

```python
# doorae_sdk/integrations/base.py
async def handle_llm_error(self, exc: Exception, room_id: UUID):
    await self.client.send(
        room_id=room_id,
        content=f"[시스템] 에이전트 {self.agent_name}가 LLM 오류로 응답하지 못했습니다: {type(exc).__name__}",
        metadata={"event": "llm_error", "error_type": type(exc).__name__},
    )
```

### 7.3.4 MCP 도구 실패

에이전트가 외부 MCP 서버(GitHub, Jira 등)에 호출 실패:

- 에이전트 엔진이 자체 retry (Claude Code SDK는 3회 기본)
- 최종 실패 시 에이전트는 LLM에게 에러를 컨텍스트로 전달하고 다른 도구로 우회하거나 유저에게 설명
- 채팅 서버는 이 과정을 **알 필요도, 알 수도 없음**
- 도구 결과가 채팅 메시지로 공유될 때만 metadata에 성공/실패 표기:

```json
{
  "content": "GitHub API 호출 실패: rate limit exceeded. 5분 후 재시도합니다.",
  "metadata": {"tool_source": "github", "status": "error", "retry_at": "..."}
}
```

### 7.3.5 에이전트 프로세스 크래시

에이전트 uvx 프로세스가 OOM 등으로 죽음:

1. 서버의 `ConnectionManager`가 WebSocket 끊김 감지 → 참여자 상태 `disconnected` 마킹
2. 운영 계층(systemd/supervisor)이 에이전트 프로세스 재시작
3. 에이전트가 재시작되면 `~/.doorae/agents/{name}.yaml` 프로필 재로드 → 이전에 참여했던 Room에 재접속
4. `?since_seq=N` (에이전트는 로컬에 last_seq 유지)로 놓친 메시지 수신
5. 몇 초의 다운타임 후 정상 복귀

**운영 권장 사항**:
- systemd user unit에 `Restart=on-failure`, `RestartSec=5`
- supervisor의 `autorestart=true`
- OOM 방지: 에이전트 프로세스당 메모리 제한 (systemd `MemoryMax=`)

## 7.4 성능 목표

### 7.4.1 목표 워크로드

Plan A의 워크로드를 이 구현이 달성해야 할 기준으로 확정:

| 항목 | 목표 값 |
|---|---|
| 동시 에이전트 수 | **50대** |
| 동시 Room 수 | **20개** |
| 평균 메시지 처리량 | **10 msg/s** |
| 피크 메시지 처리량 | **30 msg/s** (10배 burst) |
| 동시 WebSocket 연결 | **200개** (50 에이전트 + 150 유저) |

이 수치는 Doorae의 현실적 초기 운영 시나리오(소규모 팀 ~100명, 스프린트 단위 Room ~20개)에 기반한다.

### 7.4.2 서버 내부 SLO (Service Level Objectives)

| 지표 | p50 | p99 | 측정 방법 |
|---|---|---|---|
| 메시지 라우팅 지연 (WS 수신 → fanout 전송) | <5ms | **<50ms** | `doorae_ws_fanout_duration_seconds` |
| DB write 지연 (Message INSERT) | <2ms | **<10ms** | `doorae_db_write_duration_seconds` |
| 재연결 복구 지연 (since_seq 쿼리 + 전송) | <20ms | **<200ms** | `doorae_reconnect_recovery_duration_seconds` |
| WebSocket 핸드셰이크 지연 | <50ms | **<500ms** | `doorae_ws_handshake_duration_seconds` |
| E2E 메시지 지연 (발신자 send → 수신자 receive) | <10ms | **<100ms** | 분산 트레이싱 |

**강조**: 이것은 **서버 내부**의 지연이다. 에이전트의 LLM 호출 지연(1-10초)은 포함하지 않는다. LLM 지연은 엔진의 문제이지 서버의 문제가 아니다.

### 7.4.3 리소스 목표

단일 프로세스 서버 기준:

| 자원 | 목표 |
|---|---|
| 메모리 (idle) | **<80MB** |
| 메모리 (200 WS 연결 + 50 Room) | **<200MB** |
| CPU (idle) | <1% |
| CPU (피크 30 msg/s) | <20% (단일 코어 기준) |
| DB 크기 증가율 | ~1MB / 10K 메시지 |

uvicorn 단일 워커로 충분히 달성 가능한 수치다. 다중 워커가 필요한 시점이 **단일 프로세스를 떠날 시점**이기도 하다 (Plan C 마이그레이션 트리거).

### 7.4.4 측정 방법

`/metrics` 엔드포인트에서 Prometheus 지표 수집:

```python
# doorae/observability/metrics.py
from prometheus_client import Counter, Histogram, Gauge

MESSAGE_SENT = Counter(
    "doorae_messages_sent_total",
    "Total messages sent",
    ["room_id"],
)

WS_FANOUT_DURATION = Histogram(
    "doorae_ws_fanout_duration_seconds",
    "WebSocket fan-out duration",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0],
)

WS_ACTIVE_CONNECTIONS = Gauge(
    "doorae_ws_active_connections",
    "Active WebSocket connections",
)

DB_WRITE_DURATION = Histogram(
    "doorae_db_write_duration_seconds",
    "Database write duration",
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.05],
)

ERRORS = Counter(
    "doorae_errors_total",
    "Errors by category",
    ["category"],  # llm_error, db_error, ws_error, auth_error, ...
)
```

부하 테스트로 SLO 달성 여부 검증 (상세는 08-operations.md 참조).

## 7.5 한계와 마이그레이션 트리거

이 구현이 **한계에 부딪혔을 때** 어떤 신호가 나타나는지, 그리고 어디로 옮겨가야 하는지:

### 7.5.1 수평 확장 필요 신호 → Plan C 차용

- p99 메시지 라우팅 지연이 **100ms 이상** 지속적 초과
- WebSocket 동시 연결 **500개 이상**
- 단일 프로세스 CPU **80% 이상** 지속
- 동시 Room 수 **100개 이상**

이 신호가 보이면 Plan C의 NATS JetStream 백본 패턴을 차용하여 서버 인스턴스를 수평 확장한다. 마이그레이션 비용 ~550줄, 2-3주 (Plan A 섹션 13.2 참조).

### 7.5.2 감사/규제 요구 신호 → Plan B 차용

- 규제 문서에 "모든 메시지 이력 보존 + 재현 가능성" 명시
- 컴플라이언스 감사 일정 확정
- `DELETE FROM messages` 같은 데이터 수정이 절대 금지되어야 함

이 경우 Plan B의 Append-only Event Store 패턴으로 마이그레이션. 비용 ~1,200-1,500줄, 6-8주.

### 7.5.3 다중 조직 연동 신호 → Plan B federation 차용

- 파트너사/자회사와 Room 공유 요구
- 다른 Doorae 인스턴스와 메시지 교환 필요

Plan B의 Matrix federation 어댑터 차용 (~300줄 별도 모듈).

### 7.5.4 진짜 바이너리 배포가 절대 요구 신호 → Go rewrite

- 배포 타깃이 Python 설치 불가 환경 (임베디드, 제한된 OS 등)
- 시작 시간 <100ms 필수
- 메모리 <20MB 필수

이 경우 서버를 Go로 rewrite (4-6주 투자). Python SDK는 그대로 유지.

**중요**: 위 4가지 신호가 모두 오기 전까지는 이 구현으로 충분하다. 성급한 최적화나 과잉 엔지니어링을 피하라.

## 7.6 요약

| 질문 | 답 |
|---|---|
| 서버 재시작 시 메시지 유실? | 없음 (SQLite WAL) |
| 네트워크 끊김 후 재연결 시 놓친 메시지 복구? | Yes, `?since_seq=N` |
| LLM API 실패 시 서버 동작? | 관여 안 함, 에이전트 엔진 책임 |
| 에이전트 크래시 시 복구? | systemd 재시작 + 재연결 |
| 목표 워크로드 | 50 에이전트 / 20 Room / 10 msg/s |
| 단일 프로세스로 충분한가? | **Yes**, 이 워크로드에서는 넉넉 |
| 언제 떠나야 하는가? | §7.5 마이그레이션 트리거 4종 중 하나 도달 시 |

"서버는 얇게, 복구는 명확히"가 이 장의 한 줄 요약이다.

---

## 7.7 Machine 계층 장애 시나리오 (§10 관련)

§10에서 도입된 Machine 스케줄링 계층은 추가적인 장애 지점을 만든다. 각 장애의 책임 주체와 복구 방식:

| 장애 | 탐지 | 책임 주체 | 복구 방식 |
|---|---|---|---|
| **Daemon 프로세스 크래시** | 서버 WS 끊김 | systemd | `Restart=on-failure` + `RestartSec=10` → Daemon 재시작 → WS 재등록 |
| **Machine 호스트 네트워크 단절** | heartbeat 60초 미수신 | 서버 + Daemon | 서버: `status='unreachable'` 마킹. Daemon: 지수 백오프 재연결 |
| **Machine 호스트 전체 다운** | heartbeat 타임아웃 | admin + 스케줄러 | `reschedule` 정책인 agent는 다른 Machine으로. 나머지는 `unreachable` 유지 |
| **Agent subprocess OOM/크래시** | Daemon SIGCHLD | Daemon + 스케줄러 | Daemon이 `agent_crashed` 보고 → 서버가 `restart_policy` 따라 재시작 |
| **Daemon이 Agent를 spawn 실패** | `agent_spawn_failed` 수신 | 스케줄러 | 다른 Machine으로 재배치 시도 (engine capability가 맞으면) |
| **서버 재시작** | 모든 WS 끊김 | 모두 | Daemon/Agent 각자 재연결 + DB 상태 재조정 |
| **좀비 agent (DB는 running, Daemon heartbeat에 없음)** | 서버 주기 cleanup | 서버 | 3회 연속 누락 시 `crashed`로 전환 후 재시작 정책 적용 |
| **분열 (Daemon 재연결 후 상태 불일치)** | `register` 시점 | 서버 | DB와 Daemon heartbeat 목록 비교 후 교정 |

### 7.7.1 "Agent는 Daemon과 독립적이다" 원칙

**핵심 보호 장치**: Machine Daemon이 잠시 죽어도 Agent 프로세스들은 **자신의 WebSocket**으로 서버와 직접 연결되어 있으므로 채팅은 중단되지 않는다.

```
시간 T+0s:  Daemon 프로세스 crash (OOM)
         Agent-A는 여전히 /ws/rooms/X 연결 유지 → 메시지 송수신 정상
         Agent-B는 여전히 /ws/rooms/Y 연결 유지 → 메시지 송수신 정상

시간 T+10s: systemd가 Daemon 재시작
         Daemon이 /ws/machines/M 재연결
         heartbeat: running_agents=[A, B]
         서버가 DB와 대조 → 일치 → 정상 복귀
```

이 설계는 Daemon **크래시·재시작 같은 가용성 이벤트로부터** 채팅 메시지 흐름을 분리해준다. Daemon은 **제어 평면**, Agent는 **데이터 평면**이며 두 평면이 분리되어 있어, 제어 평면의 일시적 장애가 데이터 평면의 메시지 송수신을 막지 않는다.

> **보안 경계와 혼동하지 말 것**: 이 "두 평면 분리"는 **장애 격리**의 이야기이지 **보안 격리**가 아니다. Daemon이 **compromise**되면(악의적으로 제어당하면) spawn 프레임에 담긴 agent_token들을 훔쳐 Agent로 가장할 수 있으므로 채팅 메시지가 유출된다. 자세한 내용은 [§10.12.4 "Daemon은 자기가 spawn한 Agent의 신뢰 경계다"](10-machine-scheduler.md) 참조.

### 7.7.2 재배치 정책

Agent의 `restart_policy` 컬럼 3종:

| 정책 | 동작 | 용도 |
|---|---|---|
| `no_restart` | 크래시 시 종료 | 단발성 작업, 수동 재시작 원하는 경우 |
| `restart_on_same_machine` (기본) | 같은 Machine에 재시작 | 일반 운영 |
| `reschedule` | 다른 Machine으로 재배치 | Machine 자체가 불안정할 때 |

`reschedule`은 기본이 아니다. 이유: 원래 Machine이 일시 단절 후 복귀하면 "같은 agent가 두 Machine에 존재"하는 분열이 발생할 수 있다. `reschedule`은 명시적으로 활성화해야 하며, 재배치 전 5분 타임아웃을 두어 네트워크 일시 단절로 성급히 재배치하지 않는다.

자세한 시나리오, 코드, 로그 예시는 [10-machine-scheduler.md §10.11](10-machine-scheduler.md) 참조.
