# chore(release): bump anygarden-machine to 0.11.0

- Commit: `d3962c3`
- Author: Changyong Um
- Date: 2026-07-14T09:15:48+09:00
- PR: —

## Situation

`anygarden-machine` 패키지의 런타임 동작이 바뀌었으므로(세션 스토어 보존 + openhands-sdk 핀) 배포 가능한 새 버전을 끊는다.

## Task

- `anygarden-machine`을 `0.10.0` → `0.11.0`으로 bump(feat 포함 → minor).
- `anygarden-machine-v0.11.0` 태그 + GitHub 릴리즈로 표식.

## Action

`packages/machine/pyproject.toml`의 `version`을 `0.11.0`으로 갱신. 포함되는 변경:
- **#532** `feat(machines)`: codex 세션 스토어를 respawn 너머로 보존 — `.codex`를 트리째 지우던 것을 관리 파일만 prune으로 바꾸고 codex 런타임 세션(`sessions/`·`*.sqlite`·`history.jsonl`)은 보존. 제거 후 빈 `.codex/`는 삭제(no-overlay host fallback 유지), config.toml/auth.json 하드코딩 관리로 stale config wipe 유지. (#526 part 2 — part 1의 세션 매핑이 실제 resume으로 이어지게 함.)
- **#525** `fix(agent)` 사이클에 포함된 machine 변경: `openhands-sdk`/`openhands-tools` 핀을 `>=1.35,<2`로 — 상한 없는 핀이 breaking 릴리스를 조용히 끌어오는 것을 차단.

## Result

- `anygarden-machine` 0.11.0. main 병합분(#525 `ce12ca9`, #532 `697ae21`) 기준으로 릴리즈.
- 검증 요약: machine 389 passed(2 skipped), ruff clean.
- 잔여(라이브 검증): end-to-end respawn resume(#532)은 running cluster+machine+agent+LLM 필요.
