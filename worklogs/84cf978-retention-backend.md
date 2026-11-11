# wip: retention backend

- Commit: `84cf978` (84cf9785bd52d5ba7eca38a757123ed80b5aaecc)
- Author: Changyong Um
- Date: 2026-11-11T14:33:31+09:00
- PR: —

## Situation

보관(archived)된 룸의 파일은 별도 처리가 없으면 영구적으로 디스크에 남는다. 룸이 오랫동안 보관 상태를 유지하면 에이전트 아티팩트와 사용자가 올린 공유 파일이 계속 저장 공간을 차지한다. 이슈 #1018은 보관 후 일정 기간이 지난 룸의 파일을 자동으로 정리하는 보존 정책(retention policy)을 요구한다.

## Task

- `archived_room_file_retention_days` 설정값을 초과해 보관된 룸의 파일을 주기적으로 삭제하는 스위퍼(sweeper)를 구현한다.
- 에이전트 아티팩트는 바이트만 삭제하고 DB 행(tombstone)은 유지하되, `purged_at`을 기록해 링크가 404 대신 "expired"를 반환하도록 한다.
- 사용자가 올린 공유 파일은 완전히 삭제하고 에이전트들에게 삭제 팬아웃(fan-out)을 전달한다.
- 아카이브 해제 후 동일 바이트가 다시 업로드되면 tombstone 행을 복원(revive)해 이전 링크가 다시 살아나도록 한다.
- 기능을 활성화하려면 `ANYGARDEN_ARCHIVED_ROOM_FILE_RETENTION_DAYS` 환경 변수 설정이 필요하며, 기본값 `0`은 무한 보존이다.

## Action

- **`packages/cluster/anygarden/rooms/file_retention.py` (신규)**: `sweep_archived_room_files()` 함수 구현. `retention_days`일 이상 보관된 룸의 아티팩트 블롭 파일을 삭제하고 `purged_at` 기록; 공유 파일은 행까지 삭제.
- **`packages/cluster/anygarden/db/migrations/versions/107_room_artifact_purged_at.py` (신규)**: `room_artifacts` 테이블에 `purged_at` 컬럼 추가 마이그레이션.
- **`packages/cluster/anygarden/db/models.py`**: `RoomArtifact` 모델에 `purged_at: Optional[datetime]` 필드 추가.
- **`packages/cluster/anygarden/config.py`**: `archived_room_file_retention_days`(기본 `0`) 및 `file_retention_sweep_interval_sec`(기본 `3600`) 설정 추가.
- **`packages/cluster/anygarden/app.py`**: `_run_file_retention_sweeper()` 태스크 추가; 설정값이 `0`이면 비활성화. 셧다운 시 태스크 취소, 헬스 체크에 `file_retention` 항목 등록.
- **`packages/cluster/anygarden/rooms/artifacts.py`**: `revive_if_purged()` 함수 추가; `store_uploaded_artifact()` 및 `_insert_artifact()`에서 purged 행 복원 로직 호출.
- **`packages/cluster/anygarden/project_executions/artifacts.py`**: `publish_artifact()`에서도 purged 행 복원 처리.
- **`packages/cluster/anygarden/agent/runtime/attachments.py`**: `_DOWNLOAD_FAILURE_REASONS`에 HTTP 410 → `"expired"` 매핑 추가.
- **`packages/cluster/tests/test_room_file_retention.py` (신규)**: 보존 정책 단위 테스트 6개 — 기간 초과 룸만 정리, 배치 멱등성, 누락 블롭 처리, 활성 룸 보존, 복원, `0`일 비활성화.
- **`packages/cluster/tests/test_rooms_artifacts_endpoints.py`**: purged 아티팩트가 HTTP 410과 `ARTIFACT_EXPIRED` 코드를 반환하는지 확인하는 `TestExpired` 클래스 추가.

## Decisions

- **tombstone 보존 vs. 행 완전 삭제**: 아티팩트 행을 완전히 삭제하면 채팅 메시지의 링크가 dangling reference가 된다. tombstone(행 유지 + `purged_at` 설정)으로 410 "expired" 응답이 가능해 UX가 개선된다. 공유 파일은 메시지에 링크되지 않으므로 완전 삭제가 적합하다.
- **블롭 먼저 삭제 → 그 다음 `purged_at` 기록**: 반대 순서에서 크래시가 발생하면 행은 purged인데 파일은 남는 상태가 된다. 현재 순서에서 크래시 시 행은 unpurged지만 파일이 없으므로, 다음 스위프가 `purged_at` 기록을 완료한다. 멱등성이 보장된다.
- **복원(revive) 지원**: 운영자가 룸을 아카이브 해제한 뒤 같은 파일을 다시 올리면 이전 링크가 살아난다. 사용자가 혼란을 겪지 않도록 하기 위해 SHA256 기반 중복 감지 경로에 복원 로직을 삽입했다.
- **`0` = 무한 보존 (기본값)**: 기능이 명시적으로 활성화되기 전에는 기존 동작을 변경하지 않는다는 원칙. 삭제는 되돌릴 수 없으므로 opt-in이 필수적이다.

## Result

- 보관된 룸의 파일을 자동 삭제하는 백엔드 파이프라인 완성.
- `archived_room_file_retention_days > 0` 설정 시 주기적 스위퍼가 동작하며, 단위 테스트 6개로 주요 경로 검증됨.
- 아티팩트 tombstone(HTTP 410)과 공유 파일 삭제 팬아웃이 올바르게 연결됨.
- 프론트엔드 측 "expired" 표시 및 아티팩트 엔드포인트 응답 코드 반영은 아직 미완(WIP).
