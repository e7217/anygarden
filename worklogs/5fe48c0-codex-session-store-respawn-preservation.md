# feat(machines): codex 세션 스토어를 respawn 너머로 보존 (#532)

- Commit: `5fe48c0`
- Author: Changyong Um
- Date: 2026-07-14T08:57:19+09:00
- PR: —

## Situation

#526 part 1은 룸별 엔진 세션 **매핑**(codex `thread_id`)을 에이전트 cwd 파일로 durable화했다. 그러나 `.codex/*` 오버레이를 가진 codex 에이전트는 `CODEX_HOME`이 per-agent `agent_root/.codex`로 리다이렉트되고(spawner.py), 그 `.codex` 트리가 매 materialize마다 `_prune_materializer_managed_entries`로 **트리째 삭제**된다. 결과적으로 세션 전사본(`sessions/`·`state_*.sqlite`·`history.jsonl`)이 소거되어, 매핑을 복원해도 `codex exec resume`이 실패했다(→ fresh 폴백). part 2는 세션 스토어 자체를 respawn 너머로 보존해 part 1의 매핑이 실제 resume으로 이어지게 한다.

조사에서 확인: claude는 머신이 `CLAUDE_CONFIG_DIR`을 설정하지 않아 세션이 host `~/.claude/projects/<cwd-hash>`에 저장된다(cwd=agent_root는 respawn마다 동일 → 이미 생존). 따라서 대상은 **codex-오버레이 에이전트**로 한정된다.

## Task

- `.codex` 트리째 삭제를 멈추고 codex 런타임 세션 상태를 보존한다.
- 단, 기존 요구를 훼손하지 않는다: (a) manifest에서 빠진 stale 엔진 config는 여전히 wipe(codex가 낡은 MCP 오버레이를 읽지 않도록), (b) 오버레이·런타임이 모두 없어 비게 된 `.codex/`는 제거(no-overlay codex가 host `~/.codex`로 fallback), (c) stale-symlink 방어 유지.

## Action

`packages/machine/anygarden_machine/spawner.py`:
- `_SESSION_BEARING_MANAGED = frozenset({".codex"})` 추가 — 런타임 세션을 보유해 whole-prune 대상에서 제외할 관리 top-level.
- `_prune_materializer_managed_entries`가 `managed_session_dir_relpaths`를 받아, 세션-보유 dir는 `_prune_managed_files_within`로 **관리 파일만** 제거하고 나머지는 보존. 그 외 관리 항목은 기존 whole-entry prune 유지.
- `_prune_managed_files_within`: 지정 관리 relpath만 `_remove_tree_entry`(심링크 미추종)로 제거하고, 제거 후 dir가 비면 `rmdir`(요구 (b)).
- `_materialize_agent_dir`가 `managed_codex_relpaths = 현재 manifest .codex/* ∪ {.codex/config.toml, .codex/auth.json}`를 계산해 전달. config.toml/auth.json을 **하드코딩 관리**로 두어, 이전 manifest에 접근 불가(daemon이 spawn 前 덮어씀)해도 manifest에서 빠진 stale config가 항상 wipe되게 함(요구 (a)).

테스트 `test_spawner.py::TestSessionStorePreservation`:
- `test_codex_session_store_survives_rematerialize`: 재-materialize가 `sessions/`·`state.sqlite`·`history.jsonl`을 보존하고 `config.toml`을 갱신함을 단언(핵심).
- `test_non_session_managed_dirs_still_wiped_wholesale`: `.claude`/`.gemini` stray 파일은 여전히 whole-prune됨을 단언(#532가 `.codex`에만 적용됨 회귀 가드).

## Result

- machine **389 passed**(2 skipped), ruff clean. 기존 `test_prune_wipes_engine_config_when_removed`(stale config wipe + 빈 `.codex` 제거)도 빈-dir 삭제 로직으로 계속 통과.
- 보안 불변식 유지: 관리 파일 제거는 심링크 미추종, materialize 쓰기는 safefs `O_NOFOLLOW`; 보존 런타임 파일로의 materialize 쓰기 경로 없음.
- 알려진 한계(문서화): config.toml 외 **임의의** `.codex/*` 오버레이가 manifest에서 제거되면 stale로 남을 수 있음(이전 manifest 접근 불가). 주 오버레이인 config.toml은 항상 refresh되므로 실무 영향 미미.
- 잔여: end-to-end respawn resume(실제 codex-오버레이 에이전트 kill→respawn 후 보존 스토어로 resume)은 running cluster+machine+agent+LLM 필요 — 라이브 검증 대상. 선행: #526.
