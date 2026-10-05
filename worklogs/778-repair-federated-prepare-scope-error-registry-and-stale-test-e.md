# fix(projects): repair federated prepare scope, error registry and stale test expectations (#778)

- Commit: `7673482`
- Author: Changyong Um
- Date: 2026-10-05
- PR: —

## Situation

`feat/project-room-autonomy`를 main 위로 rebase한 뒤 cluster 전체 스위트에서 14건이 실패했다. rebase 전 원본 커밋에서도 같은 14건이 실패해서, 기능 작업이 원래 안고 있던 실패로 확인됐다. 이 브랜치는 수동 QA로만 검증되어 백엔드 테스트가 돌려진 적이 없었다.

## Task

- 14건 각각이 실제 결함인지, 의도된 변경에 테스트가 따라가지 못한 것인지 가르기
- 실제 결함은 코드를, 의도된 변경은 테스트 기대값을 고치기
- 같은 종류의 어긋남이 다시 생기지 않도록 회귀 테스트 추가

## Action

실제 결함 2건 (코드 수정):
- `anygarden/federation/remote_execution.py::RemoteScope`: 에이전트 런타임의 `SessionScope`에 생긴 `project_execution_id`, `input_revision`, `retry_request_id`, `retry_attempt` 필드를 추가하고, `key` 해시 규칙(재시도 필드가 모두 None이면 해시에서 제외)을 똑같이 맞춤. 연합 실행 테스트 9건 해결
- `anygarden/api/v1/errors.py`: `PROJECT_EXECUTION_TASK_MANAGED`를 `PUBLIC_ERROR_CODES`에 등록. 오류 레지스트리 테스트 1건 해결
- `tests/test_remote_scope_parity.py` (신규): 두 scope의 필드 목록과 `key`가 같은지 고정하는 테스트 4개

의도된 변경 4건 (테스트 기대값 갱신):
- `test_endpoint_transport.py`: 가짜 서버의 `token_grant`가 데몬의 `request_id`를 되돌려줌. 실제 서버(`ws/machine_handler.py`)는 이미 되돌려주고 있음
- `test_mcp_templates_self_default.py`: Codex 자체 MCP 설정에 턴 식별 헤더(환경변수 이름)와 `default_tools_approval_mode: approve` 추가. 토큰 미노출 확인은 유지
- `test_mcp_server_create_skill.py`, `test_pi_self_tools_bridge.py`: 도구 목록에 프로젝트 실행 도구 10개 추가

## Decisions

- 실패를 무조건 기대값 갱신으로 처리하지 않고, 실패마다 운영 경로를 추적했다. 그 결과 14건 중 10건이 실제 결함이었다.
  - 연합 실행 9건: 실행 워커가 예외를 `execution_deferred`로 삼키는 구조라, 테스트에서는 타임아웃으로만 보였다. 운영에서는 모든 연합 실행이 시작되지 못하는 결함이다.
  - 오류 레지스트리 1건: 운영에서 409 대신 500이 나가는 결함이다.
- `RemoteScope`는 에이전트 쪽 정의를 import하지 않고 필드를 복제했다. cluster가 agent 패키지의 런타임 내부 모듈에 의존하지 않게 하려는 기존 구조를 유지했다. 대신 parity 테스트로 두 정의가 함께 바뀌도록 강제한다.
- 기대값을 갱신한 4건은 각각 코드상의 근거를 확인했다: 실제 서버의 `request_id` 에코, `merge.py` docstring의 새 설정 형태, `PROJECT_TOOL_NAMES` 정의.
- 재검토 조건: `SessionScope`에 필드가 또 추가되면 parity 테스트가 실패한다. 그때 `RemoteScope`를 함께 고쳐야 한다. 실행 워커가 TypeError 같은 프로그래밍 오류까지 재시도 대상으로 삼키는 구조는 결함을 숨기므로 별도 개선을 검토할 만하다.

## Result

- cluster 2,773 passed, 2 skipped / agent 682 passed / frontend 빌드 성공, vitest 851 passed.
- 남은 일(#778): project_executions와 인박스 API의 백엔드 테스트(QA-03·06·08·13), 실행 관리 태스크 수정 시 409를 확인하는 엔드포인트 테스트.
