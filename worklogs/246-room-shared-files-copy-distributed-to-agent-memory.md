# feat(rooms,agent,machine,frontend): room shared files copy-distributed to agent memory (#246)

- Commit: `82ca337` (82ca337fbf687f4e90e141fd1a8304db037a3cce)
- Author: Changyong Um
- Date: 2026-04-23T01:39:58+09:00
- PR: #246

## Situation

Doorae는 텍스트 메시지만 지원해 룸에 파일/문서를 첨부하고 참여 에이전트들과 같은 컨텍스트로 논의할 방법이 없었다. 사용자는 "이 스펙 파일을 같이 보면서 Claude Code랑 Codex한테 의견 달라고 하고 싶다" 같은 일상적 요구를 매번 **본문에 텍스트를 복사해 넣기**로 우회하고 있었다.

`cross-engine file memory`(#237)가 `~/.doorae/agents/<id>/memory/notes.md`를 에이전트 장기 메모리로 관리하고 있었지만, 이건 **개인 메모**(에이전트↔서버 양방향 sync)이지 룸 단위로 공유되는 자료가 아니다. 그래서 룸 공유 자료는 아예 별도 경로로 분리 운영할 필요가 있었다.

## Task

1. 룸에 파일을 업로드/목록/삭제하는 REST API + 프론트엔드 UI.
2. 업로드된 파일을 룸 참여 에이전트들의 `memory/` 아래 **복사 배포**하고, 엔진 시스템 프롬프트가 이를 인식하게 함.
3. 에이전트 새 참여 시 backfill, 제거 시 targeted delete, 서버/머신 재시작에 대한 복구 — 파일-DB 원자성 포함.
4. 기본 SQLite 환경에서 `doorae.db` 팽창 없이 운영 가능한 저장 구조.
5. 1차 범위는 텍스트 계열 파일(≤256KB), MIME 화이트리스트.

## Action

**서버 저장·서비스**
- `packages/cluster/doorae/config.py`: `room_files_dir: Path` 필드 추가 (기본 `~/.doorae/room_files`, env `DOORAE_ROOM_FILES_DIR`).
- `packages/cluster/doorae/db/models.py`: `RoomSharedFile` 모델 신규. 원문 컬럼 없음 — `storage_path`, `sha256`, 크기/MIME/업로더만. `UniqueConstraint(room_id, storage_name)`, `uploaded_by`는 `SET NULL`.
- `packages/cluster/doorae/db/migrations/versions/031_room_shared_files.py`: 테이블 생성 + `ix_room_shared_files_room_id`.
- `packages/cluster/doorae/rooms/file_storage.py` (신규): 임시 경로 쓰기 → sha256 계산 → `os.replace` atomic rename. `save_upload`/`delete_file`/`read_file`/`cleanup_orphans` + `FileTooLargeError`.
- `packages/cluster/doorae/rooms/shared_files.py` (신규): upsert 업로드(기존 id 재사용해 디스크 경로 안정화), 삭제, `fan_out_write`/`fan_out_delete`/`backfill_agent`, `sanitize_storage_name`. 256KB 상한, 텍스트 계열 MIME 화이트리스트.
- `packages/cluster/doorae/rooms/router.py`: `POST/GET/DELETE /api/v1/rooms/{id}/files`. 참여자만 접근(`_require_room_participant`), fan-out은 FastAPI `BackgroundTasks`로 응답 후 수행. `add_participant`에 신규 agent 참여 시 `_schedule_shared_files_backfill`, `remove_participant`에 에이전트 제거 시 `_schedule_shared_files_delete_for_agent` 훅.
- `packages/cluster/doorae/app.py`: 기동 시 `cleanup_orphans`로 DB에 없는 고아 파일·`.tmp/` 잔존물 정리.

**머신 프로토콜·daemon**
- `packages/machine/doorae_machine/protocol/frames.py`: server→machine 신규 프레임 `AgentMemorySharedFileWriteFrame`(agent_id/storage_name/content/content_sha256), `AgentMemorySharedFileDeleteFrame`. `ServerFrame` Union + `_SERVER_FRAME_MAP` 등록.
- `packages/machine/doorae_machine/daemon.py`: 두 핸들러 추가. write는 `memory/shared/` mkdir 후 UTF-8 write, 기존 파일 sha256이 `content_sha256`과 같으면 skip(재전송 멱등). delete는 `unlink(missing_ok=True)`. path traversal 방지로 `storage_name`에 `/`·`.`·`..` 거부. `_handle` 디스패치에 두 case 추가.
- `packages/machine/doorae_machine/spawner.py`: cold start 시 `memory/shared/` mkdir(0o700). 기존 `notes.md` 생성 바로 옆.

**에이전트 프롬프트 주입**
- `packages/agent/doorae_agent/memory/compose.py`: `compose_shared_context_block(shared_dir)` 신규. 파일을 이름 오름차순 정렬해 `<file name="…" sha256="…">` 블록으로 감싸 `<shared-context>` 렌더링. 디렉토리 없음/빈 상태/비 UTF-8 파일은 방어적으로 skip.
- `packages/agent/doorae_agent/memory/__init__.py`: 새 함수 export.
- `packages/agent/doorae_agent/integrations/base.py`: `compose_memory_suffix`에 `Path.cwd() / "memory" / "shared"`를 주입하도록 통합. 3개 엔진 어댑터가 동일 헬퍼를 이미 쓰고 있어 추가 배선 불필요.

**프론트엔드**
- `packages/cluster/frontend/src/lib/roomFiles.ts` (신규): `uploadRoomFile`/`listRoomFiles`/`deleteRoomFile` + `RoomSharedFile` 타입. `lib/api.ts`의 강제 `Content-Type: application/json`을 피하기 위해 직접 `fetch`.
- `packages/cluster/frontend/src/components/MessageInput.tsx`: `Paperclip` 아이콘 버튼 + hidden file input. 업로드 성공 시 입력창 위에 첨부 pill(제거 가능), 실패는 인라인 빨간 텍스트. 전송 시 메시지 `metadata.references: [{type:"shared_file", id, name}]` 포함. 텍스트 없이 첨부만 있어도 전송 가능(`📎 파일명`을 content로 생성).
- `packages/cluster/frontend/src/components/MessageBubble.tsx`: `metadata.references` 중 `shared_file` 항목을 paperclip 배지로 표시.
- `packages/cluster/frontend/src/components/RoomSharedFilesDialog.tsx` (신규): 룸 공유 파일 목록 + 삭제(확인 프롬프트). 룸 설정에 맞춰 DESIGN.md warm-neutral 팔레트.
- `packages/cluster/frontend/src/pages/ChatPage.tsx`: 메시지 입력창 바로 위 "공유 파일" 링크 버튼 + Dialog 상태/렌더링.

**문서·테스트**
- `docs/design/13-room-shared-files.md`: 데이터 흐름, 저장 레이아웃, 한계, 실패 모드, 백업, future work.
- `packages/cluster/tests/test_rooms_file_storage.py` (13 tests), `test_rooms_shared_files.py` (16 tests, membership 훅·sanitize·upsert·MIME/size 포함), `packages/machine/tests/test_daemon.py::TestSharedFileHandlers` (7), `test_protocol_frames.py::TestSharedFileFrames` (2), `test_materialize.py` (1), `packages/agent/tests/test_memory_shared.py` (8).
- `packages/cluster/tests/test_migrations.py`: alembic head 단언을 030→031으로 업데이트.

## Decisions

`.tmp/plan-246-room-shared-files.md`의 §3.2 의사 결정 과정을 참고. 핵심:

1. **저장 위치 — DB vs 디스크 (plan 변경 이력 참고)**: 초안에서는 DB `Text` 컬럼에 원문을 두려 했으나 Doorae 기본 DB가 SQLite(`config.py:11`)임을 재확인하면서 전환. SQLite는 단일 writer 락·DB 파일 팽창이 실질 부담이고, `~/.doorae/` 아래 파일 기반 구조(`agents/`, `machine.toml`)가 이미 관행이라 `room_files/`를 같은 뿌리에 두는 것이 구조 일관성 + 운영 복잡도 모두 유리. 원자성은 표준 패턴(임시 경로 쓰기 → sha256 → `os.replace` → DB commit, 실패 시 unlink + 기동 시 `cleanup_orphans`)으로 해결.
2. **메모리 서브디렉토리 분리 (`memory/shared/` 독립)**: `notes.md`는 에이전트가 런타임에 append하며 양방향 sync 되는 살아있는 파일이고, 공유 자료는 서버만 쓰는 정적 파일. 두 생명주기를 한 파일에 섞으면 갱신 충돌을 hash-sync가 구분하지 못해 디렉토리로 물리 분리. sync-back 제외 로직이 **경로 기반 단순 분기**로 성립.
3. **fan-out 트리거 — 업로드 응답 내 동기 vs 백그라운드 vs 별도 워커**: FastAPI `BackgroundTasks`로 응답 즉시 반환 + 분리 수행. 룸당 에이전트 ≤5 가정에서 별도 워커 인프라는 과함. 머신 오프라인 대응은 멱등 프레임 + backfill/resync 훅으로 커버.
4. **파일명 충돌 → upsert (덮어쓰기)**: 사용자 직관(같은 이름 다시 올리면 새 내용 대체) + DB `UniqueConstraint(room_id, storage_name)` 강제. 버전 관리는 범위 밖.
5. **디스크 파일명 = uuid**: path traversal 회피, OS별 파일명 제약 무시. 사람 읽을 이름은 DB `filename`·`storage_name`과 에이전트 `memory/shared/<storage_name>`에 유지.
6. **기각된 대안 — 중앙 저장소 + 심링크**: 저장/sync 비용은 1/N로 주는 대신 (a) 에이전트 격리 상실(원본 1개 오염 → 전원 영향), (b) 엔진별 심링크 처리 편차, (c) daemon sync-back 제외 판별 복잡도, (d) 읽기 전용 enforcement 추가 구현 필요. MVP 구간에서는 복사 방식의 "blast radius 제한"과 "기존 hash-sync 경로 재사용"이 압도적. 저장 비용이 실제 병목이 되는 시점에 하이브리드로 이행.

**가정 / 미해결**
- `Path.cwd() == agent_root`: spawner 관행에 기대는 가정. 어긋나면 shared block이 주입되지 않음 — 문서화해둠.
- 런타임 중 새 shared 파일 반영: `compose_memory_suffix`가 매 메시지 전 호출된다는 가정이 맞는지 엔진 어댑터별로 검증 필요. 어긋나면 "다음 세션부터 반영" UX로 축소 + 명시적 refresh 프레임 도입이 재검토 트리거.
- 머신 재연결 훅: `resync_machine` 서비스 함수만 구현하고 WS handler 호출부는 미배선. 다음 PR에서 연결 예정.

## Result

**관찰 가능한 변경**
- `POST /api/v1/rooms/{id}/files` 가 참여자에게 열려 있고, 업로드 즉시 DB 메타 + 디스크 원문이 만들어지고, 룸에 놓인 에이전트의 `~/.doorae/agents/<id>/memory/shared/<filename>`로 **다음 세션부터** 반영됨.
- 프롬프트에는 `<memory>` 다음에 `<shared-context>` 블록이 sha256 포함으로 붙음(3 엔진 공통).
- 룸 헤더 링크에서 목록 조회 + 삭제 가능. 삭제 시 각 에이전트의 복사본도 동기화됨.
- `~/.doorae/doorae.db` 크기는 공유 파일 업로드량에 영향받지 않음 (의도대로 DB에는 메타만).

**테스트**
- cluster 728 passed / machine 304 passed / agent 280 passed (pre-existing `test_openai` 1건은 `OPENAI_API_KEY` 환경변수 부재 — main과 동일).
- 프론트엔드 `npm run build` (tsc + vite) 통과.
- alembic upgrade head → downgrade 030 → 재upgrade head 양방향 검증.

**남은 일**
- 머신 재연결 훅에 `resync_machine` 배선.
- 런타임 중 파일 반영(현재는 "다음 세션부터")을 명시적 refresh 프레임으로 보강할지 판단.
- 실제 브라우저에서 파일 첨부→전송→에이전트 응답까지 골든 패스 수동 검증.
