# fix(agent): codex-cli resume가 -s/-C 인자로 실패하는 버그 수정 (#498)

- Commit: `8c43172`
- Author: Changyong Um
- Date: 2026-06-24
- PR: #498

## Situation

#496에서 `codex-cli`(codex exec) 엔진을 추가하고 단위 테스트 20개는 통과했으나, **실전 E2E 검증**(실제 codex 바이너리로 멀티턴)에서 resume 세션이 이어지지 않는 것이 드러났다. turn2가 turn1의 맥락(이름)을 기억하지 못하고("말하지 않았습니다"), turn2 thread_id가 turn1과 달랐다 — 매 턴 새 세션이 생성되고 있었다.

## Task

- codex-cli 멀티턴에서 `resume`로 세션이 실제로 이어지게 한다.
- 같은 부류의 실패가 다시 조용히 묻히지 않도록 관측성을 보강한다.

## Action

systematic-debugging으로 수동 재현(`codex exec resume <id> -s ... -`)한 결과 `error: unexpected argument '-s' found`를 확인 — `codex exec resume`는 `-s`/`-C`를 받지 않는 exec 전용 인자였다. 어댑터가 resume에도 이를 붙여 returncode=2 → "세션 만료"로 오인 → 새 세션 재시도. 수정(`packages/agent/anygarden_agent/integrations/codex_cli.py`):

- `_resolve_codex_cli_args`: `-s <sandbox>` → `-c sandbox_mode=<sandbox>` (approval은 기존대로 `-c approval_policy=`). exec/resume 모두 수용.
- `_exec_once`: cmd에서 `-C <agent_root>` 제거 — subprocess `cwd=agent_root`가 두 경로의 작업 디렉토리를 설정.
- resume 비정상 종료 시 `logger.warning("codex_cli.resume_nonzero", stderr=...)`로 stderr 노출.
- `tests/test_integrations/test_codex_cli.py`의 tier 매핑 기대값 4건을 `-c sandbox_mode=`로 갱신.

## Decisions

- **sandbox 전달: `-c sandbox_mode=` vs resume 분기에서 `-s` 생략** → config 형태 채택. `-s`를 resume에서 빼고 exec에만 두면 분기가 갈라져 유지보수가 나빠지고, resume가 세션 sandbox를 상속하는지에 대한 가정이 필요하다. `-c sandbox_mode=`는 exec/resume **둘 다** 수용함을 실측으로 확인했으므로 단일 코드 경로로 통일하는 것이 단순하고 안전하다.
- **`-C` 제거 vs resume 분기에서만 제거** → 완전 제거. subprocess `cwd=agent_root`가 이미 동일 효과를 내므로 `-C`는 exec에서도 불필요한 중복이었다. 두 경로를 같은 인자 세트로 유지.
- **resume 실패 처리: 조용한 retry 유지하되 stderr 로깅 추가** → 원래 "thread_id 있고 returncode≠0이면 무조건 새 세션"이 인자-형태 버그(이번 건)를 "세션 만료"로 가려 디버깅을 지연시켰다. retry 동작(만료된 세션 복구)은 유지하되 stderr를 warning으로 남겨, 다음에 유사 버그가 즉시 보이게 했다.
- 가정: `codex exec resume`가 `-c sandbox_mode=`를 계속 수용. 향후 codex CLI가 config 키를 바꾸면(예: `sandbox_mode` 폐기) 재검토 필요 — `codex_cli.resume_nonzero` 경고가 그 신호가 된다.

## Result

E2E 재검증 PASS: `-c sandbox_mode=workspace-write`로 exec→resume 시 turn2 thread_id가 turn1과 동일(`019ef78b-…`), turn2가 이름("창용")을 기억, `cached_input_tokens` 누적으로 히스토리 이어짐 확인. 단위 20 passed, agent 전체 509 passed, ruff 통과. codex-cli가 멀티턴 세션을 SDK codex와 동등하게 보존한다.
