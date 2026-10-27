# fix(agents): save the workspace switches on an agent (#951)

- Commit: `e3f9650`
- Author: Changyong Um
- Date: 2026-10-27
- PR: —

## Situation

에이전트 설정의 **대화로 워크스페이스 관리** / **새 에이전트 제안** 체크박스(#938)를 켜면 잠깐 켜졌다가 바로 해제됐다. 오류 표시는 없었다. 라이브 노드(`192.168.100.247:8000`)에서 API로 재현: `PUT /api/v1/agents/{id}`가 200과 `workspace_tools: true`를 돌려주지만 다시 조회하면 `false`.

## Task

- 두 설정이 실제로 저장되게 한다.
- 응답만이 아니라 저장까지 확인하는 회귀 테스트를 둔다.

## Action

- `update_agent`는 `runtime_changed` / `peer_metadata_changed` / `archive_changed` 중 하나가 켜졌을 때만 commit한다. #938의 워크스페이스 플래그 루프는 어떤 플래그도 켜지 않아, 응답에는 메모리 값이 실리고 세션 종료 시 롤백됐다. 프론트가 직후 `GET /agents`로 다시 맞추면서 체크가 풀렸다.
- `settings_changed` 플래그를 추가해 commit 조건에 넣었다. 두 설정은 `tools/list`가 매 호출 읽으므로 respawn·로스터 전파는 하지 않는다.
- `test_only_an_admin_turns_workspace_tools_on`에 새 세션으로 DB를 다시 읽는 단언을 추가했다.

## Result

- 수정 전 코드에서 테스트 실패, 수정 후 통과 확인.
- `tests/workspace` + `tests/test_agents_api.py` 139 passed.
- 잔여: 라이브 노드는 배포·재시작 후 반영된다.
