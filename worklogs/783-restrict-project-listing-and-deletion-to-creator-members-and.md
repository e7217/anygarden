# fix(projects): restrict project listing and deletion to creator, members and admins (#783)

- Commit: `7bb3f04`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

모든 룸이 private인데도, 게스트가 아닌 사용자는 누구나 전체 프로젝트 목록을 보고 아무 프로젝트나 삭제할 수 있었다(`api/v1/projects.py`). 코드 주석에도 "프로젝트 단위 권한이 모델링되지 않아 누구나 삭제 가능"이라고 적혀 있었다. 삭제하면 그 안의 룸·참여자·메시지가 cascade로 사라진다. 프로젝트에는 생성자 정보가 없어서, 목록을 멤버십으로만 걸러도 방금 만든 빈 프로젝트가 만든 사람에게서 사라지는 문제가 있었다.

## Task

- ROOM-01: 프로젝트 조회와 삭제에 권한 적용
- 제품 결정(사용자 확인): 생성자를 기록하고 멤버십과 함께 사용. 목록은 생성자·룸 참여자·전역 admin, 삭제는 생성자·전역 admin
- 생성자가 없는 기존 프로젝트를 안전하게 처리
- 권한 없는 사용자에게 삭제 메뉴 숨김

## Action

- `anygarden/db/migrations/versions/082_project_created_by.py`: `projects.created_by` (nullable, FK users, `ON DELETE SET NULL`, SQLite용 batch 모드)
- `anygarden/db/models.py`: `Project.created_by`
- `anygarden/api/v1/projects.py`
  - 생성 시 호출 사용자를 `created_by`로 기록 (에이전트가 만들면 NULL)
  - 목록: 전역 admin은 전체, 그 외는 `created_by == 나` 또는 내가 참여한 룸이 있는 프로젝트 (에이전트는 `agent_id` 기준)
  - 삭제: 생성자 또는 전역 admin. 멤버는 403, 프로젝트를 볼 수 없는 사용자는 404
  - `ProjectOut`에 `created_by`, `can_delete` 추가
- 프론트엔드: `useRooms.ts`의 `Project` 타입에 필드 추가, `Sidebar.tsx`는 `can_delete`일 때만 프로젝트 메뉴(삭제 단일 항목) 표시
- 테스트: `tests/test_project_authz.py` (신규 6개, `@req("ROOM-01")`), `test_migrations.py`에 082 up/down 테스트, `Sidebar.test.tsx`에 메뉴 표시 테스트. head 리비전을 고정한 기존 테스트(`test_migrations.py`, `test_federation_trust.py`)를 082로 갱신

## Decisions

- 선택지 (사용자에게 확인):
  - 생성자 기록 + 멤버십 (마이그레이션 필요)
  - 마이그레이션 없이 제한 (빈 프로젝트 이름은 전원에게 노출, 삭제는 모든 룸의 owner/admin)
  - 목록 공유, 삭제만 admin
- 생성자 기록을 택했다. 빈 프로젝트가 생성자에게 보이면서 남에게는 숨겨지는 유일한 방법이고, 삭제 권한의 근거가 명확하다.
- 기존 프로젝트는 생성자를 추정하지 않고 NULL로 둔다. 추정(예: 첫 룸의 owner)은 틀릴 수 있고, 틀리면 권한을 잘못 넘긴다. 대신 admin만 삭제할 수 있다.
- 볼 수 없는 프로젝트의 삭제 시도는 404로 응답해 존재 여부를 드러내지 않는다. 볼 수 있는 멤버에게는 이유를 알려주도록 403을 준다.
- 마이그레이션 번호: `feat/project-room-autonomy`(#778)가 082–089를 쓰고 있지만, 사용자 결정에 따라 보안 수정을 main에 먼저 082로 넣고 기능 브랜치를 083–090으로 재번호한다.
- 재검토 조건: 프로젝트 단위 역할(프로젝트 admin, 소유권 이전)이나 공개 룸이 도입되면 이 규칙을 그 모델로 옮겨야 한다.

## Result

- 다른 사람의 프로젝트는 목록에 보이지 않고 삭제할 수 없다. 삭제 메뉴는 권한이 있을 때만 보인다.
- 검증: 새 권한 테스트 6개 중 5개가 수정 전 엔드포인트에서 실패하고 수정 후 모두 통과(나머지 1개는 생성자·admin 삭제 허용 경로). cluster 전체 2,701 passed(head 고정 테스트 갱신 후), 프론트엔드 빌드와 Sidebar 테스트 14개 통과. `req_report.py` 결과 ROOM-01 pass(6).
- 후속: #778 브랜치의 마이그레이션 082–089를 083–090으로 재번호.
