# fix(auth): throttle failed logins and make first-admin and invite-use races atomic (#790)

- Commit: `70ff9cd`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

기능 인벤토리에서 인증 쪽 P1 공백 세 개가 확인됐다. (1) 로그인 실패에 아무 제한이 없었다. (2) 첫 사용자 admin 판정이 "사용자 수 조회 → 삽입" 순서라, 빈 DB에 동시 가입하면 둘 다 admin이 될 수 있었다. (3) 초대 `max_uses` 확인과 `use_count+1`이 분리돼 동시 수락 시 한도를 넘을 수 있었다.

## Task

- AUTH-02: 무차별 대입을 막는 로그인 실패 제한
- AUTH-01, AUTH-06: 확인과 갱신 사이의 경쟁 제거
- 제약: 새 인프라(Redis 등) 없이 단일 프로세스 노드에서 동작

## Action

- `anygarden/auth/routes.py`
  - `LoginFailureLimiter`: 정규화한 이메일별 슬라이딩 윈도우. 15분에 실패 5회면 429와 `Retry-After`. 성공하면 초기화. 존재하지 않는 이메일도 똑같이 센다. 인스턴스는 `app.state.login_limiter`에 둔다
  - 가입: 사용자를 `is_admin=False`로 삽입한 뒤, 같은 트랜잭션에서 "다른 사용자가 없을 때만" admin으로 바꾸는 조건부 UPDATE
  - 게스트 수락: `revoked_at IS NULL AND (max_uses IS NULL OR use_count < max_uses)` 조건의 UPDATE 한 번으로 사용 횟수 차감. 영향 행이 없으면 401. 기존의 별도 증가 코드는 제거
- `tests/test_auth_races.py` (신규 4개, `@req` AUTH-01·02·06). 파일 SQLite에 요청을 동시에 보낸다

## Decisions

- 로그인 제한 키: IP 대신 이메일을 택했다. 프록시 뒤에서는 IP가 하나로 합쳐질 수 있고, 무차별 대입의 표적은 계정이다. 대신 한 사용자를 고의로 잠그는 공격이 가능해진다. 15분 창이라 영향이 제한적이라고 판단했다.
- 존재하지 않는 이메일도 같은 방식으로 세서, 429 응답으로 계정 존재 여부를 알 수 없게 했다.
- 제한기를 모듈 전역이 아니라 `app.state`에 두었다. 테스트 사이에 상태가 새지 않게 하려는 것이고, 초대 생성 제한(모듈 전역)과는 다른 선택이다.
- 경쟁 해결은 잠금이나 재시도 루프 대신 조건부 UPDATE의 영향 행 수로 판정했다. SQLite에서는 쓰기 트랜잭션이 직렬화되어 확인과 갱신이 원자적이 된다.
- 한계: 동시 가입 테스트는 수정 전 코드에서도 통과했다. 테스트 환경에서는 요청이 사실상 순서대로 처리되어 경쟁이 재현되지 않는다. 이 테스트는 결과(admin 1명)만 확인한다. 다른 세 테스트는 수정 전 코드에서 실패한다.
- 재검토 조건: 다중 워커나 다중 레플리카로 운영하면 로그인 제한기를 공유 저장소로 옮겨야 한다. Postgres를 지원하게 되면 첫 admin 판정의 격리 수준을 다시 확인해야 한다.

## Result

- 로그인 실패 5회 이후 429. 초대 동시 수락 6건 중 정확히 2건만 성공(`max_uses=2`).
- 새 테스트 4개 통과. 이 중 3개는 수정 전 코드에서 3회 연속 실패. cluster 전체 2,784 passed.
