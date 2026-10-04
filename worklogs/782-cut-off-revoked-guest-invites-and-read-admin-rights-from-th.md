# fix(auth): cut off revoked guest invites and read admin rights from the DB (#782)

- Commit: `834bd75`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

기능 인벤토리를 만들며 코드를 다시 읽다가 인증 공백 두 개를 확인했다. (1) 초대를 회수해도 그 초대로 발급된 게스트 JWT는 만료 전까지 유효했다. 초대의 `revoked_at`은 수락 시점에만 확인했다. (2) `is_admin`을 JWT 클레임에서 읽어서, DB에서 admin 권한을 빼거나 계정을 지워도 토큰 만료(최대 24h)까지 admin 라우트가 열려 있었다. (2)는 현재 admin 권한을 바꾸는 API가 없어 제품 경로로는 악용되지 않지만, 계정 관리가 생기는 순간 결함이 된다.

## Task

- AUTH-08: 회수된 초대의 게스트를 HTTP, WS 핸드셰이크, 이미 열린 소켓 모두에서 차단
- AUTH-04: admin 여부와 계정 존재를 매 요청 DB 기준으로 판정
- 제약: admin 판정이 여러 곳(`dependencies.py`, `machines.py`, `rooms/authorization.py`, `/me`)에서 클레임을 읽으므로 호출부를 하나씩 고치지 않고 공통 진입점에서 해결

## Action

- `anygarden/auth/dependencies.py::get_identity`
  - 게스트: `RoomInviteLink.revoked_at`을 조회해 회수됐거나 초대가 없으면 401 "Invite has been revoked"
  - 사용자: `User.is_admin`을 조회해 행이 없으면 401, 클레임과 다르면 `dataclasses.replace`로 DB 값을 반영
- `anygarden/ws/manager.py`: 구독에 `invite_id`를 기록하고 `revoke_invite(invite_id)`로 해당 초대의 소켓을 4001로 닫음
- `anygarden/ws/handler.py`: 게스트 구독 시 클레임의 `invite_id` 전달
- `anygarden/api/v1/invites.py::revoke_invite`: 회수 후 `connection_manager.revoke_invite` 호출
- `tests/test_auth_revocation.py` (신규 7개, `@pytest.mark.req("AUTH-04" | "AUTH-08")`)
- DB 행 없이 토큰을 만들던 기존 테스트 5개 파일이 실제 사용자·초대 행을 만들도록 수정 (`test_auth`, `test_rooms`, `test_message_reactions`, `test_federation_trust`, `test_delegation_status_endpoint`)

## Decisions

- 선택지: (1) admin 라우트 의존성(`get_admin_identity`)에서만 DB 재확인, (2) 공통 `get_identity`에서 클레임을 DB 값으로 갱신, (3) 짧은 수명 토큰과 refresh 토큰 도입.
- (2)를 택했다. (1)은 `rooms/authorization.py::is_global_admin`처럼 클레임을 직접 읽는 다른 경로를 놓친다. (3)은 프론트엔드 인증 흐름까지 바꿔야 해서 범위가 크다.
- 비용: 사용자 요청마다 기본키 조회 1회가 늘어난다. 요청 처리가 이미 DB를 쓰므로 수용했다.
- 열린 소켓 처리: 게스트 사용자 행과 초대 사이에 DB 연결이 없어, 연결 시점 클레임의 `invite_id`를 구독(메모리)에 기록했다. 다중 워커 배포에서는 다른 워커의 소켓이 닫히지 않지만, 재연결과 다음 요청은 토큰 검사로 막힌다.
- 재검토 조건: 다중 워커 운영을 지원하게 되면 소켓 회수를 워커 간에 전파해야 한다. 요청량이 커져 인증 조회가 병목이 되면 짧은 TTL 캐시를 검토한다.

## Result

- 회수된 초대의 게스트는 다음 요청과 재연결에서 401을 받고, 열린 소켓은 즉시 4001로 닫힌다.
- DB에서 admin 권한을 빼면 기존 토큰으로도 admin 라우트가 403이다. 삭제된 사용자의 토큰은 401이다.
- 검증: 새 테스트 7개는 수정 전 코드에서 모두 실패하고 수정 후 통과. cluster 전체 2,701 passed, 2 skipped. `req_report.py` 결과 AUTH-04 pass(3), AUTH-08 pass(4).
