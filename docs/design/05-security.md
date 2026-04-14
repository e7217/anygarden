# 05. 보안 — JWT + API Token + Room 스코프 권한

> **한 줄 요약**: 유저는 JWT(HS256, 24시간), 에이전트는 API Token(argon2 해시, Room 스코프), Room별 3단계 역할(observer/member/admin). FastAPI Dependency 체인으로 구현 ~125줄.

Plan A §9의 보안 모델을 **FastAPI Dependency 체인**으로 구체화한다. 구현 포인트는 "복잡한 RBAC를 만들지 않는 것"과 "토큰 탈취 시 영향 범위 최소화"다.

---

## 5.1 인증 주체와 방식

| 주체 | 인증 방식 | 토큰 위치 | 라이프타임 | 재발급 |
|---|---|---|---|---|
| **유저** | JWT (HS256) | `Authorization: Bearer ...` 헤더 (HTTP) 또는 `Sec-WebSocket-Protocol: doorae.v1, bearer.<token>` (WS) | 24시간 | 이메일+비밀번호 재로그인 |
| **에이전트** | API Token (랜덤 64바이트 base64, argon2 해시 저장) | 동일 | 90일 (기본) | admin이 재발급 |
| **관리자** | 유저 JWT + `admin` 스코프 | 동일 | 24시간 | 재로그인 |

**왜 서로 다른 방식인가**:

- **유저**는 재로그인이 자연스럽다. JWT 만료가 짧아도 UX에 큰 영향이 없다.
- **에이전트**는 장기 실행 프로세스다. 24시간마다 재인증은 운영 부담이다. 90일 API Token이 적합하다.
- **두 방식을 하나로 통일하지 않는 이유**: JWT를 90일로 설정하면 보안 노출 기간이 길어지고, API Token을 24시간으로 설정하면 에이전트가 끊임없이 재연결한다.

---

## 5.2 `doorae/auth/` 모듈 구조

```
doorae/auth/
├── __init__.py
├── jwt.py                # JWT 인코딩/디코딩 (유저용)
├── token.py              # API Token 생성/검증 (에이전트용)
└── dependencies.py       # FastAPI Dependency 체인
```

### 5.2.1 `doorae/auth/jwt.py`

```python
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from uuid import UUID
from jose import jwt, JWTError
from pydantic import BaseModel
from doorae.config import get_settings


class InvalidToken(Exception):
    """JWT 검증 실패. dependencies.py에서 401로 매핑된다."""


class UserClaims(BaseModel):
    sub: UUID          # user_id
    email: str
    is_admin: bool = False
    exp: int           # epoch seconds


def create_user_token(*, user_id: UUID, email: str, is_admin: bool = False) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = UserClaims(
        sub=user_id,
        email=email,
        is_admin=is_admin,
        exp=int((now + timedelta(hours=settings.auth.jwt_expire_hours)).timestamp()),
    ).model_dump(mode="json")
    return jwt.encode(payload, settings.auth.jwt_secret, algorithm="HS256")


def verify_user_token(token: str) -> UserClaims:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.auth.jwt_secret, algorithms=["HS256"])
    except JWTError as e:
        raise InvalidToken(str(e)) from e
    return UserClaims.model_validate(payload)
```

### 5.2.2 `doorae/auth/token.py`

```python
import secrets
from uuid import UUID
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from doorae.db.models import AgentToken  # 별도 테이블

_HASHER = PasswordHasher()
TOKEN_PREFIX = "agt_"


def generate_token() -> str:
    """에이전트용 랜덤 API Token 생성.

    Returns plaintext token — 발급 시 한 번만 사용자에게 보여준다.
    DB에는 argon2 해시만 저장된다.
    """
    raw = secrets.token_urlsafe(48)  # 64 chars
    return f"{TOKEN_PREFIX}{raw}"


def hash_token(plaintext: str) -> str:
    return _HASHER.hash(plaintext)


def verify_token_hash(plaintext: str, hashed: str) -> bool:
    try:
        _HASHER.verify(hashed, plaintext)
        return True
    except VerifyMismatchError:
        return False


async def resolve_agent_token(
    db: AsyncSession, plaintext: str
) -> AgentToken | None:
    """DB의 모든 agent token 후보 중 매칭되는 것을 찾는다.

    매칭 전략: plaintext의 prefix 8자로 candidate 조회 후 argon2 verify.
    (prefix 힌트 컬럼을 별도로 두어 O(1) lookup 근사)
    """
    if not plaintext.startswith(TOKEN_PREFIX):
        return None
    hint = plaintext[:12]  # "agt_" + 8 chars
    candidates = await db.execute(
        select(AgentToken).where(AgentToken.lookup_hint == hint)
    )
    for tok in candidates.scalars():
        if verify_token_hash(plaintext, tok.token_hash):
            return tok
    return None
```

**`AgentToken` 테이블**:

```python
class AgentToken(Base):
    __tablename__ = "agent_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"))
    token_hash: Mapped[str] = mapped_column(String(256))       # argon2 (§10.12.3와 동일 컬럼명)
    lookup_hint: Mapped[str] = mapped_column(String(16), index=True)  # 빠른 찾기
    scoped_room_ids: Mapped[list] = mapped_column(JSON, default=list)  # [] = 전체
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
```

### 5.2.3 `doorae/auth/dependencies.py`

FastAPI Dependency로 인증 체인을 구성한다.

```python
from datetime import datetime
from typing import Annotated
from uuid import UUID
from fastapi import Depends, Header, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from doorae.db.engine import get_db
from doorae.db.models import Participant, Room
from doorae.auth.jwt import verify_user_token, InvalidToken
from doorae.auth.token import resolve_agent_token


class Identity:
    """인증된 주체를 추상화한다 — user 또는 agent."""
    def __init__(self, subject_id: UUID, kind: str, is_admin: bool = False):
        self.subject_id = subject_id
        self.kind = kind  # "user" | "agent"
        self.is_admin = is_admin


async def get_identity(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    # WebSocket은 `Sec-WebSocket-Protocol` subprotocol 헤더를 사용한다.
    # 쿼리 파라미터는 access log/프록시 로그/브라우저 히스토리/Referer에
    # 토큰이 노출되므로 절대 사용하지 않는다.
    sec_websocket_protocol: Annotated[str | None, Header()] = None,
) -> Identity:
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    elif sec_websocket_protocol:
        # 클라이언트가 보낸 subprotocol 리스트에서 `bearer.<token>` 추출.
        # 예: "doorae.v1, bearer.eyJhbGciOi..."
        for p in (s.strip() for s in sec_websocket_protocol.split(",")):
            if p.startswith("bearer."):
                raw = p[len("bearer."):]
                break

    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing token")

    # 유저 JWT 시도
    try:
        claims = verify_user_token(raw)
        return Identity(claims.sub, "user", claims.is_admin)
    except InvalidToken:
        pass

    # 에이전트 API Token 시도
    tok = await resolve_agent_token(db, raw)
    if tok is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if tok.expires_at and tok.expires_at < datetime.utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token expired")

    return Identity(tok.agent_id, "agent", is_admin=False)


async def require_room_member(
    room_id: UUID,
    identity: Annotated[Identity, Depends(get_identity)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Participant:
    # Room 스코프 토큰 체크 (에이전트만 해당)
    if identity.kind == "agent":
        tok = await _latest_token_for(db, identity.subject_id)
        if tok.scoped_room_ids and room_id not in tok.scoped_room_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "room out of scope")

    # Participant 조회
    part = await db.execute(
        select(Participant).where(
            Participant.room_id == room_id,
            Participant.subject_id == identity.subject_id,
            Participant.subject_kind == identity.kind,
        )
    )
    participant = part.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not a member")
    return participant


async def require_room_sender(
    participant: Annotated[Participant, Depends(require_room_member)],
) -> Participant:
    """observer는 메시지를 보낼 수 없다."""
    if participant.role == "observer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "observer cannot send")
    return participant


async def require_room_admin(
    participant: Annotated[Participant, Depends(require_room_member)],
) -> Participant:
    if participant.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return participant


async def require_admin_user(
    identity: Annotated[Identity, Depends(get_identity)],
) -> Identity:
    if identity.kind != "user" or not identity.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return identity
```

### 5.2.4 엔드포인트에서의 사용 예시

```python
# doorae/rooms/router.py
from fastapi import APIRouter, Depends
from doorae.auth.dependencies import require_room_member, require_room_admin

router = APIRouter(prefix="/api/v1")


@router.get("/rooms/{room_id}/messages")
async def list_messages(
    room_id: UUID,
    participant: Annotated[Participant, Depends(require_room_member)],
    db: Annotated[AsyncSession, Depends(get_db)],
    before_seq: int | None = None,
    limit: int = 50,
):
    # participant가 확보된 시점에서 이미 권한 검증 완료
    ...


@router.delete("/rooms/{room_id}")
async def delete_room(
    room_id: UUID,
    _: Annotated[Participant, Depends(require_room_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    ...
```

**Dependency 체인의 이점**: FastAPI가 자동으로 의존성 그래프를 해결하므로 각 엔드포인트 코드에는 `Depends(require_room_sender)` 한 줄만 있으면 된다. 권한 누락 실수가 발생하기 어렵다.

---

## 5.3 권한 모델 — 3단계 역할

Room별로 참여자가 가지는 역할. 단순하지만 견고하다.

| 역할 | 읽기 | 쓰기 | 서브 채널 생성 | Room 관리 | 자식 Room 열람 |
|---|---|---|---|---|---|
| `observer` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `member` | ✓ | ✓ | ✓ (부모 member 이상) | ✗ | ✗ |
| `admin` | ✓ | ✓ | ✓ | ✓ (초대/제거/아카이브) | ✓ (감사용) |

**정책이 정말로 3개면 충분한가**:

- MVP에서는 충분하다. Discord/Slack도 초기에는 비슷한 수준에서 시작했다.
- 세밀한 권한 매트릭스 (메시지 삭제·편집·고정·검열·reactions)는 **v2로 미룬다**.
- 더 복잡한 요구가 생기면 Plan B의 5단계 권한(regulator/auditor 추가)을 참고한다.

---

## 5.4 Room 스코프 토큰

에이전트 API Token은 "어떤 Room에 접근할 수 있는지"를 토큰 자체에 기록한다. 토큰 탈취 시 영향 범위를 최소화하는 핵심 메커니즘이다.

### 스코프 정책

| 스코프 | 의미 | 사용 예 |
|---|---|---|
| `scoped_room_ids = []` | 전체 Room 접근 (무제한) | 운영 admin, 로그 수집기 |
| `scoped_room_ids = ["room_a", "room_b"]` | 지정 Room만 접근 | 일반 에이전트 (권장) |

**기본값**: 토큰 발급 시 스코프를 **반드시** 지정해야 한다. 무제한 토큰은 `--unscoped` 플래그를 명시적으로 줘야 생성된다.

### CLI 예시

```bash
# 범용 에이전트 (운영 admin, 무제한)
uvx doorae-client admin token create \
  --agent-name LogCollector \
  --unscoped \
  --expires-in 30d

# 일반 에이전트 (권장: Room 스코프)
uvx doorae-client admin token create \
  --agent-name PM \
  --scoped-rooms room_sprint_42,room_sprint_43 \
  --expires-in 90d
```

응답:

```
Agent: PM
Token: agt_xxx...xxxx  (발급 시 한 번만 표시됨)
Scoped rooms: [room_sprint_42, room_sprint_43]
Expires: 2026-07-06
```

---

## 5.5 서브 채널의 권한 상속

Plan A §7.6 재확인. 서브 Room 생성 시점에 권한 체크가 일어난다.

```python
# doorae/rooms/service.py (발췌)
async def create_sub_room(
    db: AsyncSession,
    *,
    parent_room_id: UUID,
    creator_participant: Participant,
    ...
) -> Room:
    # 부모 Room의 member 이상이 아니면 거부
    if creator_participant.role == "observer":
        raise NotMember("observer cannot create sub-room")

    # 부모 Room의 project_id를 자식이 상속
    parent = await db.get(Room, parent_room_id)
    child = Room(
        project_id=parent.project_id,  # 스코프 상속
        parent_room_id=parent_room_id,
        ...
    )
    ...
```

**admin 자동 열람**: 부모 Room의 admin은 자식 Room의 메시지를 읽을 수 있다.

```python
# doorae/rooms/router.py 내 list_messages 쿼리 확장
async def check_read_access(
    db: AsyncSession, identity: Identity, room: Room
) -> bool:
    # 직접 참여자면 통과
    if await is_direct_member(db, identity, room.id):
        return True
    # 부모 Room의 admin이면 통과 (자식 Room 감사 열람)
    if room.parent_room_id:
        parent_part = await get_participant(db, identity, room.parent_room_id)
        if parent_part and parent_part.role == "admin":
            return True
    return False
```

---

## 5.6 감사 로그 — 자연스러운 설계

**별도의 감사 로그 테이블을 만들지 않는다**. 이유:

1. `messages` 테이블이 이미 모든 발언을 `seq` 단조 증가 순서로 저장한다. 이것이 곧 감사 로그다.
2. `participants` 테이블이 누가 언제 join/leave했는지 추적한다 (감사 열람 가능).
3. `rooms` 테이블이 생성/아카이브 시각을 가진다.

**질문 예시와 SQL**:

> Q: "어제 오후 3시 ~ 5시에 PM 에이전트가 sprint-42 Room에서 뭘 말했는가?"

```sql
SELECT m.seq, m.content, m.created_at
FROM messages m
JOIN participants p ON m.participant_id = p.id
JOIN agents a ON p.subject_id = a.id AND p.subject_kind = 'agent'
WHERE a.name = 'PM'
  AND p.room_id = 'room_sprint_42'
  AND m.created_at BETWEEN '2026-04-05 15:00:00' AND '2026-04-05 17:00:00'
ORDER BY m.seq;
```

복잡한 감사 요구(규제 · 불변성 · 시간 여행)가 생기면 Plan B의 Event Store로 승격한다. 그때까지는 이 단순 구조로 충분하다.

---

## 5.7 전송 계층 (TLS)

| 환경 | 프로토콜 | 인증서 |
|---|---|---|
| 로컬 개발 | `ws://localhost:8000` | 없음 |
| 단일 VPS | `wss://doorae.example.com` | Let's Encrypt (Caddy 자동) |
| K8s | `wss://...` | cert-manager |

**nginx/Caddy 프록시 설정 예시** (Caddy 권장, 자동 TLS):

```caddy
doorae.example.com {
    reverse_proxy 127.0.0.1:8000 {
        # WebSocket 업그레이드 필수
        header_up Connection Upgrade
        header_up Upgrade websocket
    }
}
```

nginx 버전:

```nginx
location /ws/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400s;  # 장시간 유지
    # Sec-WebSocket-Protocol 헤더는 기본적으로 전달되지만 커스텀 proxy 구성에서는
    # 아래처럼 명시해야 할 수 있다.
    proxy_set_header Sec-WebSocket-Protocol $http_sec_websocket_protocol;
}
```

### 5.7.1 WebSocket 인증은 반드시 subprotocol 헤더로 — 쿼리 금지

WebSocket 인증에서 **토큰을 URL 쿼리 파라미터로 전달하는 것은 보안 안티패턴**이다. 쿼리 문자열은 다음 경로로 새어나간다:

- **서버 access log**: nginx/Caddy가 기본적으로 전체 URI를 기록
- **프록시/CDN log**: Cloudflare, AWS ALB 등 중간 경유지
- **브라우저 히스토리**: SPA 앱의 경우 URL이 그대로 저장
- **Referer 헤더**: 페이지 내 외부 링크 클릭 시 다음 사이트로 유출
- **에러 페이지/크래시 리포트**: 스택 트레이스에 URL 포함
- **수동 디버깅 스크린샷**: 팀 채널에 공유되면 사고

그래서 이 구현은 WebSocket 인증을 **`Sec-WebSocket-Protocol` subprotocol 헤더**로만 받는다. 구체적으로는 WebSocket handshake 시 클라이언트가 subprotocol 리스트에 `doorae.v1`과 함께 `bearer.<token>`을 포함시킨다.

**브라우저 (표준 WebSocket API)**:
```javascript
// 쿼리 파라미터 NO
// const ws = new WebSocket("wss://doorae.example.com/ws/rooms/abc?token=..."); // 금지

// Sec-WebSocket-Protocol YES
const ws = new WebSocket(
    "wss://doorae.example.com/ws/rooms/abc",
    ["doorae.v1", `bearer.${token}`]
);
```

**Python (`websockets` 라이브러리)**:
```python
# 쿼리 파라미터 NO
# url = f"wss://...?token={token}"  # 금지

# subprotocols 인자 YES
async with websockets.connect(
    "wss://doorae.example.com/ws/rooms/abc",
    subprotocols=["doorae.v1", f"bearer.{token}"],
) as ws:
    ...
```

**curl 수동 테스트**:
```bash
# wscat(브라우저 wrap) 또는 websocat을 사용
websocat 'wss://doorae.example.com/ws/rooms/abc' \
    --protocol 'doorae.v1,bearer.eyJhbGciOi...'

# 또는 -H로 직접 헤더 지정
websocat 'wss://doorae.example.com/ws/rooms/abc' \
    -H 'Sec-WebSocket-Protocol: doorae.v1, bearer.eyJhbGciOi...'
```

**서버 측**:
- `doorae/auth/dependencies.py`의 `get_identity`가 `Sec-WebSocket-Protocol` 헤더에서 `bearer.` prefix를 가진 항목을 추출한다 (§5.2.3 코드 참조).
- 핸드셰이크 응답 시 선택한 subprotocol(`doorae.v1`)을 반환해야 브라우저가 연결을 수락한다:
  ```python
  await websocket.accept(subprotocol="doorae.v1")
  ```
- 토큰이 없거나 유효하지 않으면 1008 (Policy Violation)로 종료.

### 5.7.2 접근 로그 필터링

그래도 혹시 모를 실수로 쿼리 파라미터에 민감 정보가 섞일 때를 대비해 nginx/Caddy 로그에서 `token`, `password`, `secret` 같은 쿼리 파라미터는 마스킹한다:

```nginx
# nginx.conf
log_format scrubbed '$remote_addr - $remote_user [$time_local] '
                    '"$request_method $uri_scrubbed $server_protocol" '
                    '$status $body_bytes_sent';

map $request_uri $uri_scrubbed {
    ~*^(.*)(token|password|secret)=[^&]*(.*)$  $1$2=***$3;
    default                                    $request_uri;
}
```

Caddy는 기본 access log에 `request_id`와 상태 코드만 남기므로 별도 필터가 덜 필요하지만, `log` 디렉티브로 동일한 마스킹이 가능하다.

---

## 5.8 민감 데이터 처리

| 데이터 | 처리 방식 |
|---|---|
| 유저 비밀번호 | argon2 해시 (argon2-cffi) |
| 에이전트 API Token | argon2 해시 + `lookup_hint` prefix 8자 |
| JWT secret | 파일 (`~/.doorae/jwt.secret`, 600 권한) |
| DB 파일 | 파일 시스템 권한 (`~/.doorae/doorae.db`, 600) |
| 로그 | 토큰·비밀번호를 절대 로깅하지 않음 (structlog processor에서 redact) |

**`structlog` redaction 설정** (`doorae/observability/logging.py`):

```python
def redact_sensitive(logger, method_name, event_dict):
    for key in ("password", "token", "authorization", "secret"):
        if key in event_dict:
            event_dict[key] = "[REDACTED]"
    return event_dict

structlog.configure(
    processors=[
        redact_sensitive,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    ...
)
```

---

## 5.9 에이전트 내부 보안은 위임

서버는 다음에 관여하지 **않는다**:

- 에이전트가 어떤 외부 도구(MCP 서버)를 호출하는가
- 에이전트가 어떤 파일을 읽거나 쓰는가
- Human-in-the-Loop 승인 여부
- 에이전트의 프롬프트 인젝션 방어

이것들은 **각 에이전트 엔진의 책임**이다:

| 엔진 | 내부 보안 메커니즘 |
|---|---|
| Claude Code SDK | Tool allow-list, permission prompt |
| Codex SDK | Sandbox execution |
| OpenHands | Docker sandbox (에이전트 내부, 서버와 무관) |
| Deep Agents | `HumanInTheLoopMiddleware` |

이 경계가 모호해지면 서버 보안 모델이 감당할 수 없이 복잡해진다. Doorae 서버는 "메시지 허브"이지 "에이전트 샌드박스 오케스트레이터"가 아니다.

---

## 5.10 구현 체크리스트

- [ ] `doorae/auth/jwt.py`: `create_user_token`, `verify_user_token` (~40줄)
- [ ] `doorae/auth/token.py`: `generate_token`, `hash_token`, `resolve_agent_token` (~45줄)
- [ ] `doorae/auth/dependencies.py`: `Identity`, `get_identity`, `require_room_*` (~40줄)
- [ ] `doorae/db/models.py`: `AgentToken` 테이블 추가 (~15줄)
- [ ] `doorae/cli.py`: `admin token create` 명령 (~20줄)
- [ ] 테스트:
  - [ ] JWT 발급/검증 정상 경로
  - [ ] 만료된 토큰 거부
  - [ ] Room 스코프 외 접근 거부
  - [ ] observer가 send_message 시도 → 403
  - [ ] admin이 자식 Room 열람 → 허용

**총 LOC**: ~160줄 (auth 모듈 전체 + 모델 추가 + CLI). 서버 기준선 LOC 예산에 포함되어 있다.

---

## 5.11 Machine Token (§10 관련)

§10에서 도입된 **Machine 스케줄링 계층**은 제3의 토큰 카테고리인 **Machine Token**을 사용한다. User JWT와 Agent API Token과 **완전히 분리된** 인증 경로다.

### 5.11.1 세 토큰 종류 비교

| 토큰 | 주체 | 저장 위치 | 스코프 | 유효기간 | 사용 경로 |
|---|---|---|---|---|---|
| **JWT** | User | 브라우저/환경변수 | 계정 + 역할 | 24h | `Authorization: Bearer ...` (HTTP), subprotocol (WS) |
| **Agent API Token** | Agent 인스턴스 | Agent 프로세스 환경변수 `DOORAE_TOKEN` | 특정 Agent의 Room 참여 | 발급 경로에 따라 두 가지 (아래 주석) | `/ws/rooms/{id}` |
| **Machine Token** | Machine Daemon | `~/.doorae/machine.token` (chmod 600) | 자기 Machine 제어만 | 장명 (회전 권장) | `/ws/machines/{id}` |

**Agent API Token의 두 발급 경로**:
- **admin 수동 발급** (§5.1, §5.2.2): 운영자가 CLI로 생성, 기본 90일 TTL. standalone 모드 에이전트 또는 장기 실행 운영 에이전트용
- **scheduler 자동 발급** (§10.12.3): Machine 스케줄러가 `spawn_agent` 시점에 1회용으로 발급, 24h 단명 TTL. §10 Machine 경로로 spawn된 Agent용

두 경로 모두 동일한 `AgentToken` 테이블을 사용하지만 발급자/수명/재발급 정책이 다르다.

### 5.11.2 Machine Token의 직접 권한 경계

Machine Token **자체**(단독으로 사용될 때)가 할 수 있는 것과 할 수 없는 것:

- **가능**: 자기 `/ws/machines/{id}` 엔드포인트 접속, `register`/`heartbeat`/`agent_*` 프레임 송신
- **불가**: 자기 주소 바깥에서 메시지 송수신 (Agent Token 영역), Room 목록 조회 (User Token 영역), 다른 Machine 제어, admin API 호출

이 직접 권한만 놓고 보면 Machine Token은 매우 제한적으로 보인다. **하지만 이것만으로 Daemon이 안전하다고 결론내리면 안 된다.**

### 5.11.2.1 중요: Daemon 침해 = 자기가 spawn한 Agent 전체의 침해

**초기 초안은 "Daemon compromise 시에도 채팅 메시지는 유출되지 않는다"고 주장했다. 이는 잘못된 주장이었다.**

실제로는: 서버 스케줄러가 `spawn_agent` 프레임을 Daemon에 보낼 때 **agent_token을 평문으로 포함**한다 (그래야 Daemon이 그것을 subprocess의 환경변수로 넘길 수 있다). 따라서 Daemon을 침해한 공격자는:

1. 진행 중 및 향후 `spawn_agent` 프레임에서 agent_token을 평문으로 본다
2. 이미 돌고 있는 Agent subprocess의 환경변수를 `/proc/[pid]/environ`으로 덤프할 수 있다
3. 획득한 agent_token으로 **자기가 Agent인 척** `/ws/rooms/{id}`에 접속할 수 있다
4. 해당 Agent가 참여한 모든 Room의 메시지를 읽고 쓸 수 있다

이것은 **구조적 신뢰 관계**이며 피할 수 없다. Kubernetes에서 node compromise가 그 node의 Pod service account token 노출로 이어지는 것과 같은 구조다. `Machine Daemon은 자기가 spawn한 모든 Agent에 대해 trusted computing base다`.

### 5.11.2.2 대응 수단의 정직한 분류 (예방 / 폭발 반경 제한 / 탐지 / 대응)

**경고**: Daemon 침해에 대한 대응 수단은 한 묶음의 "운영 규율"이 아니다. 각 수단은 침해의 어느 단계에서 효과를 발휘하는지가 다르며, **"활발한 침해 중"에 트래픽을 실시간으로 보호할 수 있는 수단은 사실상 존재하지 않는다**. 토큰 회전과 짧은 TTL을 "mitigation"으로 오해하지 말 것 — 이들은 활발한 침해를 막지 못한다 (공격자는 회전된 새 토큰도 즉시 훔친다). 상세 분류는 [§10.12.4.3](10-machine-scheduler.md)에 있다.

네 가지 범주로 정확히 분류하면:

#### (a) 예방 — 침해 확률 낮추기 (침해 전)

- **Daemon을 비관리 유저로 실행**: systemd user unit + `NoNewPrivileges=true`. root 금지.
- **호스트 격리**: Daemon 전용 머신/VM. 다른 서비스와 공유 금지.
- **Daemon 소프트웨어 업데이트**: CVE 모니터링 + 빠른 패치.
- **신뢰할 수 있는 호스트에만 배치**: 외부 파트너 호스트, 개인 BYOD에는 Daemon 금지.
- **바이너리 무결성**: PyPI signed release, attestation.

이들은 **예방 전용**이다. 이미 침해가 진행 중이면 도움이 안 된다.

#### (b) 폭발 반경 제한 — 침해 "시" 영향 범위 축소 (침해 전 결정)

**이것이 이 문서가 제공하는 가장 강한 방어선이다. 활발한 침해 중에도 유효한 유일한 부류다.**

- **민감한 Room은 신뢰도 높은 Machine에만**: `required_labels={"trust_tier":"high"}` affinity로 강제. 한 Machine 침해가 모든 Room을 노출시키지 않음
- **Machine 분리 by tenant**: 테넌트 A Machine은 테넌트 A Agent만 spawn. 침해가 테넌트 간에 퍼지지 않음
- **profile_yaml에 비밀 값 넣지 말 것**: API 키 등은 subprocess의 별도 env 주입 또는 시크릿 스토어에서 agent가 직접 가져옴. Daemon이 훔치는 것이 "agent 토큰만"으로 축소
- **Untrusted 호스트는 standalone 모드만**: Daemon을 띄우지 말고 사용자가 `uvx doorae-agent` 직접 기동. Daemon 경로 자체가 없음

민감한 것은 **처음부터** 신뢰도 높은 소수의 Machine에만 두어야 한다. 침해가 시작된 뒤에는 이 결정을 되돌릴 수 없다.

#### (c) 탐지 — 침해 발생 인지 (침해 중)

- **`doorae_auth_anomaly_total` 지표**: 병렬 접속 급증, 가장 접속 시도 감시. **공격자가 정상 agent를 흉내 내면 효과 제한적**
- **파일 무결성 모니터링** (AIDE, Tripwire): `~/.doorae/machine.token`, 바이너리, systemd unit 해시 감시
- **외부 감사 로그 수집**: journald remote나 syslog forward로 **즉시** 외부 시스템에 복제. 침해된 Daemon은 로컬 로그를 지울 수 있지만 이미 내보낸 로그는 못 건드림

**탐지 수단만으로는 침해를 막지 못한다.** 탐지는 "얼마나 빨리 대응할 수 있는가" = 피해 시간을 결정할 뿐이다.

#### (d) 대응 — 탐지 이후 (침해 종료)

- **Machine Token 즉시 revoke**: `POST /api/v1/machines/{id}/tokens/revoke` → 서버가 Machine WS 연결 강제 종료 + blacklist
- **해당 Machine의 모든 Agent Token 일괄 revoke**: `POST /api/v1/agents/revoke-by-machine/{id}`
- **Machine draining + 포렌식 + 재설치**: 침해 의심 Machine을 draining으로 전환하고 호스트 완전 wipe 후 재설치
- **Machine Token 정기 회전**: **"예방이나 mitigation이 아니다."** 이것은 "잠재적·미탐지 침해의 유효 기간에 상한을 지우는" 백그라운드 위생 수단이다. 회전 시점마다 공격자의 구 토큰이 무효화되지만, 회전 사이에 Daemon이 여전히 침해 상태면 공격자가 새 토큰도 그대로 얻는다. 따라서 회전은 "한 번 훔치고 떠난 공격자의 토큰 수명을 제한"하는 효과만 있다
- **Agent Token 짧은 TTL** (§10.12.3 scheduler 발급 경로, 최대 24h): Machine Token 회전과 같은 논리 — 잠재적 침해 토큰의 자연 만료 시간을 설정. 활발한 침해 중에는 매번 새 토큰도 훔쳐진다

**결정적으로**: 회전과 짧은 TTL은 **"백그라운드 위생"**이지 **"active compromise 방어"**가 아니다. 이들이 효과를 발휘하는 유일한 조건은:
- 공격자가 한 번 훔친 뒤 접근이 끊어진 경우, 또는
- 사람이 개입하여 Daemon을 제거한 뒤

### 5.11.2.3 잔존 리스크 — 수용할 수밖에 없는 것

- **활발한, 미탐지 Daemon 침해 기간 동안** 해당 Machine의 모든 Agent 채팅이 실시간으로 공격자에게 노출된다. 피할 수 없다. 이 기간 = 탐지 지연 + 대응 시간. **탐지 지연을 줄이는 것이 유일하게 의미 있는 투자다**
- 호스트 root 침해 시 프로세스 메모리(`gcore`)를 덤프해서 MCP 도구 결과와 대화 컨텍스트를 훔칠 수 있다. 이것도 mitigation은 "호스트를 잘 지켜라" 외에 없다. TEE(SGX/SEV)가 있어야 진짜 격리되지만 경량 철학과 양립 불가
- profile_yaml에 이미 넣어버린 비밀은 돌이킬 수 없다. 애초에 안 넣어야 함 (§5.11.2.2 (b))

상세 분류, 공격자가 얻는 것/얻지 못하는 것, 각 수단의 침해 중 유효성은 [10-machine-scheduler.md §10.12.4](10-machine-scheduler.md) 참조.

### 5.11.3 구현 파일

- `doorae/auth/machine_token.py` (신규, ~60줄): 발급/검증/회전
- `doorae/auth/dependencies.py`에 `get_machine_identity` Dependency 추가 (~30줄)
- `doorae/db/models.py`에 `MachineToken` 테이블 추가 (~20줄)

자세한 발급 절차, 검증 로직, 회전 전략은 [10-machine-scheduler.md §10.12](10-machine-scheduler.md) 참조.

### 5.11.4 WebSocket subprotocol 헤더 (Machine도 동일)

§5.7.1의 원칙("토큰은 절대 URL 쿼리 파라미터로 전달 금지")은 Machine Daemon에도 그대로 적용된다:

```python
# doorae_machine/daemon.py — 올바른 예
async with websockets.connect(
    ws_url,
    subprotocols=["doorae.v1", f"bearer.{self.machine_token}"],  # ✓
) as ws:
    ...

# 금지
# url = f"{ws_url}?token={self.machine_token}"  # ✗ 로그 유출
```
