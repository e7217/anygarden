# fix(machine): scope CODEX_HOME redirect to agents with .codex overlay

- Commit: `7aed6f0`
- Author: Changyong Um
- Date: 2026-04-20
- PR: #213

## Situation

직전 커밋 `3a9021d` 이 codex 엔진 에이전트에 대해 `CODEX_HOME` 을 per-agent `.codex/` 로 무조건 리다이렉트했다. 같은 PR 내 Codex 리뷰 스탑훅이 "unconditional CODEX_HOME redirection regresses supported host-auth codex startup" 으로 회귀를 플래그했다. 의미: MCP 템플릿도, `engine_secrets` API key 도 없이 호스트 사용자의 `~/.codex/auth.json` (ChatGPT 로그인) 에 의존해 구동되는 정상 스타트업 경로가, 빈 per-agent `.codex/` 로 강제 리다이렉트되어 첫 턴 auth 실패를 유발하는 상태.

## Task

- `CODEX_HOME` 리다이렉트를 "오버레이 존재" 조건으로 스코핑해 오버레이 없는 codex 에이전트는 호스트 경로를 그대로 사용하도록 복구
- materialize 단에서 MCP 가 없을 때도 빈 `.codex/` 를 만들던 로직 제거 — 리다이렉트가 꺼지면 빈 디렉토리가 남을 이유가 없고, 디스크 노이즈 + CODEX_HOME 가 잘못된 빈 대상을 가리키게 되는 외형 혼란을 방지
- 테스트: 오버레이 있을 때 리다이렉트됨 + 오버레이 없을 때 리다이렉트 안 됨(호스트-auth 경로 보존의 명시적 가드) 양방향으로 고정

## Action

- `packages/machine/doorae_machine/spawner.py` `spawn()`: `has_codex_overlay = any(path.startswith(".codex/") for path in msg.files)` 를 계산해 `msg.engine == "codex" and has_codex_overlay` 조건에서만 `env["CODEX_HOME"]` 주입. 코멘트에 regression 원인과 스코핑 근거 명시
- `packages/machine/doorae_machine/spawner.py` `_materialize_agent_dir`: 직전 커밋이 추가했던 unconditional `.codex/` mkdir 블록 제거. 매니페스트에 `.codex/*` 가 있을 때는 파일-쓰기 루프의 `target.parent.mkdir + chmod 0o700` 이 이미 디렉토리를 만들어 둠
- `packages/machine/tests/test_materialize.py`:
  - `test_codex_engine_always_has_codex_dir` 를 `test_codex_engine_does_not_create_empty_codex_dir` 로 뒤집음 (빈 `.codex/` 가 **없어야** 함)
  - `test_non_codex_engines_do_not_get_empty_codex_dir` 삭제 (더 이상 특별 케이스 아님)
  - `test_prune_wipes_engine_config_when_removed` 를 원래의 "디렉토리도 함께 사라짐" 어설션으로 복귀
  - `test_prune_wipes_codex_dir_for_non_codex_engine` 삭제 (엔진 분기가 사라졌으므로 redundant)
- `packages/machine/tests/test_spawner.py`:
  - `test_spawn_sets_codex_home_for_codex_engine` → `test_spawn_sets_codex_home_when_codex_overlay_present` 로 개명 + manifest 에 `.codex/config.toml` 오버레이 포함
  - 신규 `test_spawn_does_not_set_codex_home_without_codex_overlay` — 회귀 가드: codex 엔진 + 빈 files → `CODEX_HOME` 미주입. 이게 직전 커밋이 무너뜨렸던 호스트-auth 경로의 명시적 계약
  - `test_spawn_does_not_set_codex_home_for_other_engines` — claude-code/gemini-cli 엔진에 대해 일부러 `.codex/config.toml` 을 포함시켜도 non-codex 엔진이면 절대 주입되지 않음을 확인 (openhands 는 화이트리스트 차단 우회 회피 위해 빈 files)

## Decisions

Codex 리뷰 피드백을 수용하면서 세 가지 대안을 비교:

- **A — manifest 에 `.codex/*` 있을 때만 리다이렉트** ← 선택. 신호가 명시적(admin 이 의도적으로 config 를 첨부)이고, 파일시스템 I/O 없이 `msg.files` 만 보면 판정 가능 → 테스트도 단순
- **B — materialize 후 `(agent_root/".codex").is_dir()` 로 판정**: A 와 결과 동등하나 fs 의존성 발생. 단위 테스트가 실제 디렉토리 생성에 종속
- **C — CODEX_HOME 리다이렉트하되 host auth.json 을 per-agent dir 로 심볼릭 링크**: MCP + host-auth 공존을 허용하지만, codex 가 auth 토큰 refresh 시 write-back 시도 → sandbox 가 링크 target 을 outside 로 해석해 거부 → 세션 실패 위험. 또한 여러 agent 가 동일 host 토큰을 공유하게 되어 격리 원칙과 상충. 범위 대비 리스크 큼

결정적 근거: A 는 "MCP 붙인 agent 는 engine_secrets 기반으로 가고, 붙이지 않은 agent 는 호스트 auth 로 가는" doorae 의 기본 운영 모델과 정합. admin UX 측면에서 "MCP 붙이는 순간 auth 구성도 자기 손으로" 는 자연스러운 계약.

rejected:
- "오버레이 없어도 codex 에이전트는 per-agent 격리" — 매력적이지만 auth 문제를 해결하지 않고선 호스트-auth 유저의 기존 구동 경로를 깨는 것이 즉각적 손해. 격리는 필요하면 별도 이슈로 (예: auth.json 심볼릭 링크 + sandbox 가드 재검토)

가정 (깨지면 재검토):
- admin 이 MCP 템플릿을 codex 에이전트에 붙일 때 동시에 `engine_secrets` (OPENAI_API_KEY 또는 LLM 게이트웨이 키) 도 구성한다는 운영 관례 — 이게 아니라면 "MCP 붙이기 = auth 끊김" 이 새로운 운영 함정이 됨. 현재 admin UI 가 MCP attach 와 engine_secrets 를 묶어 안내하는지 후속 확인 필요
- `msg.files` 의 `.codex/` 키가 "codex 오버레이 있음" 의 충분 조건 — 서버 `merge_codex_config` 는 MCP 템플릿이 없을 때 빈 `.codex/config.toml` 을 보내지 않음 (확인됨: `merge.py:220-237` 는 overlays 없으면 admin_content 그대로 반환, 없으면 아예 생성 자체가 skip)

## Result

- `packages/machine` 268/268 통과 (직전 커밋에서 271 → 테스트 3건 정리로 268)
- Codex 스탑훅 리뷰 재실행 시 regression 플래그 해소 예상
- 운영 영향 범위:
  - 호스트-auth 로 구동되던 기존 codex 에이전트 (MCP 미사용): 무변경 ✓
  - MCP 템플릿 첨부된 codex 에이전트: 정상 리다이렉트 + 이전과 동일하게 MCP 로드 ✓
  - 엣지: "MCP 없고 engine_secrets 도 없고 호스트 auth 도 없는" 극히 비정상 구성은 본래도 동작 불가였으므로 변화 없음
