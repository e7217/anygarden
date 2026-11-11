# feat(rooms): remove files of rooms archived past a retention period (#1018)

- Commit: `0360415` (0360415867251319d31dd51e8faefe49cfce504c)
- Author: Changyong Um
- Date: 2026-11-11T14:41:43+09:00
- PR: #1018

## Situation

보관(archived) 상태의 룸은 파일을 영구 보존하므로, 오랫동안 보관된 룸의 에이전트 아티팩트와 사용자 공유 파일이 디스크를 계속 점유한다. 방 삭제(#911)도 보관이므로 사용자가 지운 방의 파일까지 남는다. #1017로 에이전트가 올릴 수 있는 형식이 넓어지면서 정리 수단이 필요해졌다.

## Task

- `ANYGARDEN_ARCHIVED_ROOM_FILE_RETENTION_DAYS` 환경 변수(기본 `0` = 비활성)를 통해 시간별 스위퍼를 활성화한다.
- 에이전트 아티팩트: 바이트 삭제 후 `room_artifacts.purged_at` tombstone 기록; 다운로드 시 HTTP 410 `ARTIFACT_EXPIRED`.
- 사용자 공유 파일: 완전 삭제 후 에이전트에 삭제 팬아웃 전송.
- 프론트엔드: 채팅 첨부·아티팩트 패널·이미지 뷰어·인박스 모두에서 "expired" 상태를 렌더링한다.
- 복원(revive) 지원: 같은 바이트를 다시 업로드하면 tombstone 행이 살아나 이전 링크가 복원된다.

## Action

**백엔드**

- **`packages/cluster/anygarden/rooms/file_retention.py`** (신규): `sweep_archived_room_files()` — 배치 500개씩 처리, 블롭 먼저 삭제 후 `purged_at` 기록.
- **`packages/cluster/anygarden/db/migrations/versions/107_room_artifact_purged_at.py`** (신규): `room_artifacts.purged_at` 컬럼 추가 마이그레이션.
- **`packages/cluster/anygarden/db/models.py`**: `RoomArtifact.purged_at: Optional[datetime]` 추가.
- **`packages/cluster/anygarden/config.py`**: `archived_room_file_retention_days` (기본 `0`), `file_retention_sweep_interval_sec` (기본 `3600`) 설정 추가.
- **`packages/cluster/anygarden/app.py`**: `_run_file_retention_sweeper()` 태스크 등록·셧다운·헬스체크 항목 추가.
- **`packages/cluster/anygarden/rooms/router.py`**: 다운로드 엔드포인트에서 `purged_at` 체크 → HTTP 410 `ARTIFACT_EXPIRED` 반환; `RoomArtifactOut`에 `purged_at` 필드 노출.

**프론트엔드**

- **`packages/cluster/frontend/src/lib/roomArtifacts.ts`**: `RoomArtifact.purged_at` 필드 추가; `ArtifactExpiredError` 클래스 신설; `fetchArtifactBlobUrl()` 에서 HTTP 410 → `ArtifactExpiredError` throw.
- **`packages/cluster/frontend/src/components/MessageAttachments.tsx`**: `FileCard`에 `state: 'ready' | 'unavailable' | 'expired'` 타입 도입; `DownloadButton`이 `ArtifactExpiredError`를 감지하면 로컬 `expired` 상태로 전환; `HtmlPreviewButton` 프리뷰 에러도 `expired` 구분 처리; `ImageAttachment` 에서 `image.status === 'expired'` 분기 추가.
- **`packages/cluster/frontend/src/components/RoomArtifactsDialog.tsx`**: `item.purged_at`이 있으면 `ImageOff` 아이콘 + 만료 메시지를 표시하고 다운로드 버튼을 숨긴다.
- **`packages/cluster/frontend/src/i18n/catalogs/chat.ts`**: `chat.attachmentExpired` 번역 키 2개 추가.
- **테스트**: `MessageAttachments.test.tsx`에 expired 렌더링 케이스; `RoomArtifactsDialog.test.tsx`에 purged_at 아이템 케이스; `test_room_file_retention.py` 단위 테스트 6개; `test_rooms_artifacts_endpoints.py`의 `TestExpired` 클래스; `test_migrations.py`·`test_federation_trust.py`의 head 갱신. 로컬: cluster pytest 4,724 통과(무관 플레이크 1건 단독 재실행 3/3 통과), vitest 1,294 통과, alembic upgrade/downgrade 확인, 실제 sweeper 루프를 스크래치 DB로 실행해 tombstone·공유 파일 삭제 확인. 라이브 노드 검증은 미실시.

## Decisions

- **tombstone(행 유지) vs. 행 완전 삭제**: 채팅 메시지의 첨부 링크가 dangling reference가 되면 사용자 혼란이 크다. `purged_at` tombstone으로 410 `ARTIFACT_EXPIRED`를 반환해 "만료됨"을 명시적으로 전달한다. 공유 파일은 메시지에 링크되지 않으므로 완전 삭제한다.
- **블롭 먼저 삭제 → `purged_at` 나중 기록**: 순서를 역전하면 크래시 시 행은 purged인데 파일이 남는 불일치가 생긴다. 현재 순서에서 크래시 시 행은 unpurged지만 파일이 없으므로 다음 스위프가 `purged_at`을 완료한다. 멱등성이 보장된다.
- **기본값 `0` = opt-in**: 삭제는 되돌릴 수 없으므로 명시적으로 설정하지 않으면 기존 동작을 유지한다.
- **프론트엔드에서 `ArtifactExpiredError` 타입 분리**: 404(네트워크 실패)와 410(만료)을 같은 오류 처리 경로에 뭉치면 사용자에게 "다운로드 실패" 메시지를 보여주게 된다. 별도 오류 클래스로 분리해 "파일이 만료됨" 메시지를 명확히 보여준다.

## Result

- `ANYGARDEN_ARCHIVED_ROOM_FILE_RETENTION_DAYS > 0` 시 시간별 스위퍼가 동작한다.
- 보관 기간 초과 룸의 에이전트 아티팩트 다운로드는 HTTP 410 `ARTIFACT_EXPIRED`를 반환한다.
- 채팅 첨부, 아티팩트 패널, 이미지 뷰어 모두 "파일이 만료됨" UI를 표시한다.
- 에이전트 인박스(첨부 다운로드)는 410 → `"expired"` 사유로 매핑된다.
- 같은 바이트를 재업로드하면 tombstone이 복원된다.
- 25개 파일, +648 / -44 줄 변경; 단위·통합 테스트 포함.
