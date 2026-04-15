# 11. Anonymous Guest Participation

> Status: **Draft (RFC #22)**
> Owner: cluster team
> Tracking issue: https://github.com/e7217/doorae/issues/22

## 11.1 Why

`add_participant` 엔드포인트(`POST /api/v1/rooms/{room_id}/participants`)는 인증된 `User` 또는 `Agent`만 받는다. 외부 협업자가 룸에 잠시 참여하려면 회원가입을 거쳐야 하므로 호스트가 임시 채널을 열고 닫는 운영 마찰이 크다. 게스트 신원을 도입해 가입 없이도 단일 룸에 한정해 대화·에이전트 호출까지 가능하게 한다.

## 11.2 Trust model

게스트는 인증된 `User`보다 항상 약한 권한을 가지며, **단일 룸에 바인딩**된다. 신뢰의 출처는 호스트가 발급한 초대 토큰뿐이고, 토큰은 revoke·만료·max_uses로 통제된다. 게스트는 `Identity.kind="guest"`로 식별되며 모든 권한 가드는 인증된 사용자보다 게스트를 더 좁은 경로로 흐르게 한다.

## 11.3 Data model

```python
# packages/cluster/doorae/db/models.py (변경)
class User:
    id: str
    email: str | None        # 게스트는 None (NOT NULL → nullable)
    password_hash: str | None  # 게스트는 None
    is_admin: bool = False
    is_anonymous: bool = False  # 신규
    display_name: str | None = None  # 신규 — 게스트 닉네임
```

### Migration caveat
- 현재 `email`은 `nullable=False, unique=True`. nullable 전환은 안전하지만 `unique=True`는 NULL 다중 허용을 위해 partial unique index가 필요 (SQLite는 partial index 미지원 → SQLAlchemy `batch_alter_table` + 테이블 재생성으로 처리). PR A는 이 마이그레이션 스크립트까지 포함한다.
- 앱 레이어는 게스트 등록 전에 `email is not None`임을 강제하는 가드를 유지 (DB만 믿지 않는다).

```python
# 신규 테이블
class RoomInviteLink:
    id: str                    # invite_id (UUID), DB 식별자
    lookup_hint: str           # 토큰의 첫 12자 (인덱스, AgentToken 패턴)
    token_hash: str            # 토큰 전체의 hash (절대 평문 저장 금지)
    room_id: str               # FK rooms.id
    created_by: str            # FK users.id (admin/owner)
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    max_uses: int | None
    use_count: int = 0
```

### Token format
- 발급 시 32B `secrets.token_urlsafe(32)` → 클라이언트에 1회 전달.
- 서버 저장: `lookup_hint = token[:12]`, `token_hash = hash_password(token)` 같은 단방향 해시.
- 검증: hint로 candidates 조회 → `verify_token_hash`로 매칭. `AgentToken`/`MachineToken`과 동일 패턴 (`auth/token.py` 재사용).

기존 `Participant.user_id` FK는 그대로 사용한다. 게스트도 참여 시 `User` row 1개와 `Participant` row 1개가 생성되며, `Participant`에서 본 게스트는 일반 사용자와 구분되지 않는다 (UI는 `User.is_anonymous`로 배지 표시).

## 11.4 Auth flow

```
1. host  → POST /api/v1/rooms/{room_id}/invites
   ← {token, expires_at, max_uses}

2. guest browser → GET /invite/{token}  (frontend route)
3. guest         → POST /api/v1/auth/guest
                   {token, display_name}
   ← {jwt}        (claims: user_id, room_id, invite_id, is_guest, exp)

4. guest WS → wss://.../ws/rooms/{room_id}
              Sec-WebSocket-Protocol: doorae.v1, bearer.<jwt>
```

`get_identity`는 JWT를 디코드해 `is_guest=True`이면 `Identity(kind="guest", id=user_id, claims=...)`를 반환한다.

### `require_room_member` 분기 수정 (PR C 필수)
현재 `auth/dependencies.py:150-153`은 `identity.kind == "user"`가 아니면 무조건 `agent_id`로 매칭한다. 게스트가 추가되면 `kind="guest"` 분기에서 `Participant.user_id == identity.id` AND `claims.room_id == path room_id` 둘 다 검증해야 한다. 분기 누락 시 게스트가 모든 룸에서 403이 되거나, 잘못 매칭되어 에이전트 권한으로 통과될 수 있다. PR C 범위에 명시 포함.

### REST에서도 `claims.room_id` 강제
JWT는 `Authorization: Bearer` 헤더로 모든 REST 엔드포인트에 통과한다. WS만 가드해서는 안 되며, 모든 게스트-허용 REST 엔드포인트(`GET /rooms/{room_id}/messages` 등)는 `forbid_guest()`로 차단하거나 path의 `room_id`와 `claims.room_id`가 일치하는지 검증해야 한다.

## 11.5 Permission matrix

| 동작 | user | agent | guest |
|---|---|---|---|
| 메시지 발신/수신 (자기 룸) | ✅ | ✅ | ✅ |
| `@agent` 멘션 | ✅ | n/a | ✅ |
| `#room` 멘션 | ✅ | ✅ | ❌ (silently dropped) |
| 서브룸 생성 | ✅ | ✅ | ❌ |
| `add_participant` | admin/owner only | ❌ | ❌ |
| invite 발급/revoke | admin/owner only | ❌ | ❌ |
| 룸 목록/메시지 조회 (다른 룸) | ✅ | ✅ | ❌ |
| 자기 룸 메시지 단건 조회 (`/messages/{id}`) | ✅ | ✅ | **room_id 일치 시만** |
| 저장 메시지 (saved messages) | ✅ | ✅ | ❌ |
| 타이핑 표시 | ✅ | ✅ | ✅ |
| representative 자동 합류 트리거 | ✅ | n/a | ❌ |

권한 적용은 `forbid_guest()` FastAPI dependency를 신규로 만들어 admin/membership 가드와 함께 데코레이션한다. WS 측은 `ws/handler.py`에서 frame별로 분기한다.

### 메시지 단건 조회 가드
현재 메시지 조회 엔드포인트가 `require_room_member`만 쓰고 `message.room_id == path.room_id`를 명시적으로 검증하지 않으면, 게스트가 본인 룸 path로 접근하면서 query/body의 `message_id`로 다른 룸 메시지를 뽑을 여지가 있다. PR E에서 해당 핸들러들을 전수조사해 `message.room_id`와 path의 `room_id`를 대조하는 가드를 추가한다.

## 11.6 Mention filtering

`parse_mentions`의 결과를 가공한다:

```python
if identity.kind == "guest":
    mentions = [m for m in mentions if m["type"] == "agent"]
```

룸 멘션은 다른 룸으로의 라우팅 트리거이므로 게스트 격리 원칙에 위배된다. 에이전트 멘션은 해당 룸의 참여자인 에이전트만 호출되므로 격리를 깨지 않고 허용한다. 다만 representative 자동 합류 (`ws/handler.py:206-217`)는 `#room` 멘션에 걸려 있어 11.6의 필터링만으로도 자연스럽게 차단된다. 추가로 REST 경로에서 멤버십이 생성되는 모든 지점(`add_participant`, DM 자동 생성, `create_sub_room`)에 `forbid_guest()`를 덧씌워 우회 경로를 봉쇄한다.

## 11.7 Rate limiting

세 개의 독립 레이어로 구성한다. 위반 시 모두 `ErrorOut(detail="Rate limited (guest)")`로 응답.

1. **게스트 개별 cooldown** — `guest_cooldown_manager`, capacity=3, refill=0.5/s. 인증 사용자 기본값(capacity=5, refill=1/s)보다 엄격함을 유지한다.
2. **게스트 에이전트 멘션 레이트** — 게스트 participant 단위로 분당 3회 (sliding window).
3. **룸 단위 게스트 합산 레이트** — 한 룸 내 모든 게스트의 에이전트 멘션 합산이 분당 N회(초기값 20) 초과 시 드롭. invite로 동시 접속 다수 시 LLM 비용 폭주를 막기 위한 상한이다. 초과분은 게스트에게 `Rate limited (guest, room aggregate)` 에러 + 호스트에게는 표시하지 않는다.

invite 발급 자체에도 admin당 분당 10건, 룸당 활성 invite 20개 상한을 둔다 (PR B).

## 11.8 New WS frames

기존 `RoomMembershipChangedOut`을 확장해 호스트들에게도 broadcast (`action="added"`/`action="removed"`)한다. 별도 `GuestJoinedOut`은 도입하지 않는다 — 호스트 UI는 같은 프레임을 보고 게스트 여부를 `User.is_anonymous`로 분기한다.

## 11.9 Frontend

### 게스트 진입
- `/invite/:token` — 닉네임 입력 폼, `POST /auth/guest` 호출
- `/g/:roomId` — 단일 룸 셸 (사이드바 없음, "게스트로 참여 중" 배너)
- 멘션 입력 UI는 `@` 자동완성만 노출 (룸 자동완성 숨김)

### 호스트 측
- 룸 설정 패널에 "초대 링크" 섹션 (생성/복사/만료 표시/revoke)
- 참여자 목록에 게스트 배지 (`User.is_anonymous`)

## 11.10 Operational concerns

### 라이프사이클
게스트 `User`는 하드 삭제할 수 없다 — `Message.participant_id`가 `ON DELETE SET NULL`이긴 하지만 `ActivityLog`/감사 흐름에서 user_id 참조가 필요할 수 있다. 대신 아래 3단계 lifecycle으로 다룬다.

1. **active** — invite 유효 기간 동안 WS 접속 가능.
2. **revoked** — invite의 `revoked_at`/`expires_at` 도달. 즉시 WS 연결 종료, 신규 JWT 발급 차단. `Participant` 행은 유지.
3. **anonymized** (30일 cron) — `revoked` 상태가 30일 지나면 `User.display_name`을 `"(former guest)"` 같은 고정 문자열로 덮어쓰고, JWT 시크릿 롤오버에 따라 기존 토큰이 자연 만료됨을 확인. row 자체는 남긴다.

### 메트릭
| 이름 | 정의 |
|---|---|
| `doorae_guest_active` | 현재 WS 접속 중인 게스트 수 (ConnectionManager 기반, gauge) |
| `doorae_guest_registered_total` | `is_anonymous=True, revoked=False` 게스트 수 (gauge, slow path) |
| `doorae_guest_messages_total{room_id}` | 게스트가 보낸 메시지 총량 (counter) |
| `doorae_invite_created_total{room_id}` | 발급된 invite 수 (counter) |
| `doorae_invite_uses_total{invite_id}` | invite별 사용 횟수 (counter) |
| `doorae_guest_rate_limited_total{scope}` | rate limit 차단 (scope=`cooldown`/`mention`/`room_aggregate`) |

### 기타
| 항목 | 처리 |
|---|---|
| 감사 로그 | `ActivityLog`의 actor에 `is_guest` 플래그, `invite_id` 기록 |
| GDPR | 게스트는 email 없음, IP 미저장 (요청 시 hash) |
| DB 폭증 | 룸당 활성 invite 상한(20) + admin 발급 rate limit (분당 10) + invite당 max_uses 권장 |
| 토큰 유출 대응 | invite revoke API 즉시 반영, revoke 시 해당 invite로 발급된 모든 활성 게스트 JWT를 `ActivityLog.session_revoked`로 기록 |

## 11.11 Out of scope (후속)

- 게스트의 영구 계정 승격 흐름
- IP 기반 차단 / Captcha
- 다중 룸 게스트 (`room_id` 클레임을 set으로 확장)
- 게스트 간 DM
- 게스트가 본 메시지 히스토리의 redact 정책

## 11.12 Build sequence

| PR | 내용 | 의존 |
|---|---|---|
| A | `User` 컬럼 추가 + alembic 마이그레이션 (SQLite batch migration 포함, `email` partial unique는 앱 레이어 가드로 보완) | — |
| B | `RoomInviteLink` + admin invite endpoints + 발급 rate limit + 룸당 활성 상한 | A |
| C | `Identity.kind="guest"` + `POST /auth/guest` + `forbid_guest` + `require_room_member` 분기 확장 | B |
| D | WS handler 게스트 분기 (멘션 필터 + 3-레이어 rate limit) + REST 멤버십 생성 경로(`add_participant`, `create_sub_room`, DM 자동 생성)에도 `forbid_guest` 장착 | C |
| E | 룸/메시지/saved_message 조회 게스트 격리 (+ 메시지 단건 조회 `room_id` 대조 가드) | C |
| F | 호스트 invite UI (frontend) | B |
| G | 게스트 entry shell (frontend) | C, D |
| H | revoke/anonymize cron + 메트릭(B·D·E 전반) + 최종 문서 | B, D, E, G |

### Critical path: "guest-gate" (C+D+E)
PR **C가 단독 머지되면** guest JWT가 발급되는 순간부터 D/E의 가드가 없는 상태로 모든 REST·WS 엔드포인트가 `kind != "user"` 일반 분기(= agent 권한과 혼동)로 통과한다. 위험을 피하려면:

- **옵션 1 (권장)**: C + D + E를 **같은 머지 윈도우** 안에 연속 머지. 세 PR이 각각 리뷰는 받되 main 머지는 세트로.
- **옵션 2**: C에 기본 *deny-all-guests* 플래그(`DOORAE_ENABLE_GUESTS=false`)를 넣어 C 단독 머지 후 D/E 머지 완료 시점에 플래그를 켠다. 운영 쪽에서 선호.

A→C까지는 백엔드, F/G는 B/C/D 완료 후 병렬 진행 가능. H는 앞선 모든 단계의 hook을 종합한다.
