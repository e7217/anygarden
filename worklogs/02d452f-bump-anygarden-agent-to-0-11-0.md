# chore(release): bump anygarden-agent to 0.11.0

- Commit: `02d452f`
- Author: Changyong Um
- Date: 2026-07-14T07:31:18+09:00
- PR: —

## Situation

OpenHands SDK와의 비교 학습에서 도출한 세 개선(#525·#526·#527)이 main에 병합되었다. 이 중 `anygarden-agent` 패키지의 런타임 동작이 실질적으로 바뀌었으므로(엔진 어댑터 + 의존성 핀), 배포 가능한 새 버전을 끊어야 한다.

## Task

- `anygarden-agent`를 `0.10.0` → `0.11.0`으로 bump(feat 포함 → minor).
- 릴리즈 태그(`anygarden-agent-v0.11.0`) + GitHub 릴리즈로 이번 사이클의 변경을 표식한다.

## Action

`packages/agent/pyproject.toml`의 `version`을 `0.11.0`으로 갱신. 포함되는 변경:
- **#525** `fix(agent)`: openhands 어댑터를 openhands-sdk 1.35+의 `mcp_config` 스키마(`dict[str, MCPServer]`)에 맞춤 — `_manifest_to_mcp_config`로 `mcpServers` envelope를 unwrap + `coerce_mcp_config` 정규화, 생성 fallback을 `(TypeError, ValidationError)`로 확장, `openhands-sdk`/`-tools` 핀을 `>=1.35,<2`로(agent·machine).
- **#526** `feat(agents)`: 룸별 엔진 세션 매핑(codex `thread_id`/claude `session_id`)을 에이전트 cwd 파일에 저장해 respawn 너머로 durable화 — 재기동한 어댑터가 복원해 cold 대신 resume(엔진 세션 스토어가 생존하는 경우). 세션 스토어 자체 보존은 part 2로 분리.

(#527 `refactor(cluster)`는 테스트 전용이라 cluster 런타임 변경이 없어 별도 릴리즈 대상 아님. machine의 openhands 핀 변경은 이 사이클에 함께 반영됨.)

## Result

- `anygarden-agent` 0.11.0. main 병합분(#525 `ce12ca9`, #527 `32bd7a4`, #526 `e4fb50f`) 기준으로 릴리즈.
- 검증 요약(각 PR): agent 481·487 passed, openhands 엔진 64 passed, machine 387 passed, cluster 회귀 4 passed, ruff clean. 실 openhands-sdk 1.35에서 mcp_config coerce 확인.
- 잔여(라이브 검증): openhands 라이브 룸 턴(#525), respawn end-to-end resume(#526)은 running cluster+machine+agent+LLM 필요.
