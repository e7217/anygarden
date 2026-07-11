# fix(rooms): participant 룸당 유일성 보장 + 검색 FTS 부트 자가치유 (#519, #520)

- Commit: `73cbfbf` (73cbfbfa7e82fb35b8668771a2b12c7eb8a417e5)
- Author: Changyong Um
- Date: 2026-07-12T02:49:03+09:00
- PR: #519, #520

## Situation

2026-07-11 라이브 핸즈온 검증에서 실환경 결함 2건이 확정됐다. (1) 한 룸에 같은
유저의 `participants` 행이 2개 이상이면 `require_room_member`의 `scalar_one_or_none()`이
`MultipleResultsFound`를 던져 messages/read REST가 500, WS 핸드셰이크가 4003(비멤버
오판)으로 룸 전체가 브릭됐다(룸 `rr-verify-431`에서 admin이 admin+member 2행 보유).
(2) alembic 관리 DB(Case 1)에서 `messages_fts` 가상 테이블이 유실되면 부트가 재생성하지
않아 검색이 영구 503이었다(활성 DB에 테이블 부재, messages 63행). 두 결함 모두 기존
`docs/anygarden-verification-checklist.md`의 P0 항목과 일치.

## Task

- `require_room_member`가 중복 행에서 500/4003을 내지 않게 방어.
- `participants`에 룸당 유일성(사용자/에이전트)을 DB 레벨로 강제하고 기존 중복을 dedupe.
- 유일성 도입으로 `add_participant` 중복 재추가가 IntegrityError(500)가 되지 않도록 409 계약화.
- Case 1 DB에서 `messages_fts` 부재/유실을 부트가 자가치유하고 기존 메시지를 백필.
- 기존 테스트 회귀 없이 신규 계약을 테스트로 고정.

## Action

- `packages/cluster/anygarden/auth/dependencies.py` — `require_room_member`를
  `scalar_one_or_none()` → role(admin/owner) 우선 정렬 + `limit(1)` + `.scalars().first()`로 변경.
- `packages/cluster/anygarden/db/models.py` — `Participant.__table_args__`에 부분 UNIQUE 인덱스
  2개(`uq_participants_room_user` / `uq_participants_room_agent`, 각각 `… IS NOT NULL` 조건, sqlite+postgresql) 추가.
- `packages/cluster/anygarden/db/migrations/versions/052_participant_uniqueness.py` (신규) —
  ROW_NUMBER() 기반 dedupe(admin/owner 우선, joined_at/id 타이브레이크) 후 부분 UNIQUE 인덱스 생성. downgrade는 인덱스 drop.
- `packages/cluster/anygarden/rooms/router.py` — `add_participant` user 분기에 기존 멤버십
  프리체크 → 409("User is already a participant").
- `packages/cluster/anygarden/db/fts.py` — `backfill_message_fts(conn)` 추가(NOT EXISTS 가드 INSERT).
- `packages/cluster/anygarden/app.py` — `_self_heal_message_fts(engine)` 추가(SQLite 가드), Case 1
  `alembic upgrade head` 뒤 호출. `backfill_message_fts` import.
- 테스트: `test_participant_uniqueness.py`(신규 3), `test_search_fts_bootstrap.py`(self-heal 1),
  `test_rooms.py`(add_participant 3건을 별도 유저 추가로 수정 + 409 테스트 신설),
  `test_migrations.py`(head 리비전 assertion 051→052).

## Decisions

`.tmp/plan-519-520-participant-uniqueness-and-fts-selfheal.md`의 의사결정을 따름.
- **require_room_member 다건 처리**: "아무 1행"이 아니라 admin/owner 우선 반환 — 반환된
  `role`이 다운스트림 authz(마지막 admin 제거 방지 등)에 쓰여, member 행이 잡히면 권한 격하 위험.
- **부분 UNIQUE 인덱스 2개 vs 단일 복합 UNIQUE**: SQLite는 UNIQUE에서 NULL을 distinct로 취급 →
  단일 `UNIQUE(room_id,user_id,agent_id)`는 무력화. user/agent가 상호배타 nullable이라 부분 인덱스가 정확.
- **FTS: 부트 self-heal vs 신규 복구 마이그레이션**: 결함 본질이 "부트 경로가 FTS를 보장 안 함"이라,
  스탬프 무관하게 매 부트 복구하는 self-heal 채택(마이그레이션은 재유실 시 복구 불가). `fts.py`의
  "any connection에서 호출 안전" 설계 의도와 일치.
- **add_participant 409 vs 멱등**: `membership.py` docstring·이슈 #519가 "repeat add는 409"를
  계약으로 명시(프론트 의존) → 409. 기존 3개 테스트가 생성자를 자기 룸에 재추가하며 중복을
  검증하던 것을 별도 유저 추가로 교정.
- 가정: SQLite 전용(부분 인덱스/FTS5). Postgres 이관 시 `postgresql_where`로 병기했으나 재검토 필요.
  dedupe 시 삭제 행의 `pinned`/`last_read_message_seq`는 병합하지 않고 폐기(중복은 spurious 전제).

## Result

- cluster 전체 테스트 1252 passed / 0 failed, ruff 통과.
- **실제 DB 사본 검증**: BEFORE(alembic=051·중복 admin 2행·messages_fts 없음) →
  AFTER(alembic=052·1행 admin 생존·messages_fts 63 백필·`TypeScript` 검색 4건·중복 재삽입 차단).
- 중복 participant 룸의 500/4003 브릭 해소, 기존 DB 검색 503 자가치유. 라이브 서버 재기동 후
  실환경 재검증은 병합 후 후속(`docs/anygarden-hands-on-verification-2026-07-11.md` ❌ 항목 체크 전환).
