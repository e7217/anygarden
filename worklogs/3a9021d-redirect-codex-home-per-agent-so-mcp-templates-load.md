# fix(machine): redirect CODEX_HOME per-agent so MCP templates load

- Commit: `3a9021d` (3a9021d52935d0e1e95b8c3d4d04816ad5ac9757)
- Author: Changyong Um
- Date: 2026-04-20
- PR: —

## Situation

doorae 는 admin UI 에서 에이전트에 MCP 템플릿을 첨부하면 server 쪽 `merge_codex_config`(`packages/cluster/doorae/mcp_templates/merge.py`)가 `[mcp_servers.<name>]` TOML 블록으로 렌더해 spawn manifest 의 `files[".codex/config.toml"]` 로 실어 보낸다. machine 쪽 `_materialize_agent_dir`(`packages/machine/doorae_machine/spawner.py`)는 그 페이로드를 `<agent_root>/.codex/config.toml` 로 정확히 기록한다.

그런데 codex CLI / app-server 는 `$CODEX_HOME/config.toml`(기본 `~/.codex/config.toml`)만 읽고 cwd 의 `.codex/` 는 탐색 대상이 아니다. claude-code 가 cwd 의 `.mcp.json` 을 자동 발견하고 (#142 에서 맞춘 부분) gemini-cli 가 cwd 의 `.gemini/settings.json` 을 자동 발견하는 것과 달리 codex 는 "프로젝트 로컬" 탐지 규칙이 없다. 결과적으로 agent01-codex 같은 codex 엔진 에이전트가 재 spawn 되어도 MCP 서버는 한 번도 활성화된 적이 없었다 — 호스트 사용자의 `~/.codex/config.toml` 에는 `[mcp_servers]` 가 없으므로 codex 는 그냥 "mcp 없음" 으로 구동됨.

실측 증거: `/home/e7217/.doorae/agents/0b4a63f4-.../.codex/config.toml` 에 `[mcp_servers.github]` 가 정확히 기록돼 있으나, `~/.codex/config.toml` 에는 `[mcp_servers]` 섹션이 없고 `CODEX_HOME` 도 unset 상태.

## Task

- codex 엔진 에이전트의 구동 env 에 `CODEX_HOME` 을 per-agent `.codex/` 경로로 리다이렉트
- materialize 단에서 MCP 템플릿이 없는 에이전트에도 `.codex/` 디렉토리가 존재하도록 보장 (codex 가 `auth.json`/`history.jsonl`/`sessions/` 를 런타임에 그 안에 쓰므로 디렉토리가 없으면 첫 턴에서 opaque 실패)
- 다른 엔진(claude-code, gemini-cli, openhands)에는 `CODEX_HOME` 이 주입되거나 `.codex/` 가 생기지 않아야 함 — 디스크 노이즈 + 호스트 사용자의 codex 세션 간섭 방지
- prune 일관성: 재 spawn 시 codex 엔진은 `.codex/config.toml` 이 빠져도 빈 디렉토리가 재생성되고, 다른 엔진은 `.codex/` 전체가 흔적 없이 사라져야 함

## Action

- `packages/machine/doorae_machine/spawner.py` `_materialize_agent_dir`: claude-code 기본 settings 블록 뒤에 `if msg.engine == "codex"` 가드로 `agent_root / ".codex"` `mkdir(parents=True, exist_ok=True)` + `chmod 0o700` 추가
- `packages/machine/doorae_machine/spawner.py` `spawn`: `env["DOORAE_TOKEN"]` 주입 바로 뒤에 `if msg.engine == "codex" and agent_root is not None: env["CODEX_HOME"] = str(agent_root / ".codex")` 추가
- `packages/machine/tests/test_materialize.py` `TestMaterializeFresh`: `test_codex_engine_always_has_codex_dir`(codex 엔진은 빈 manifest 로도 `.codex/` 생성, 0o700), `test_non_codex_engines_do_not_get_empty_codex_dir`(claude-code/gemini-cli/openhands parametrize) 추가
- `packages/machine/tests/test_materialize.py` `TestMaterializePrune`: 기존 `test_prune_wipes_engine_config_when_removed` 를 "codex 엔진은 빈 `.codex/` 재생성" semantics 로 업데이트하고, `test_prune_wipes_codex_dir_for_non_codex_engine`(non-codex 엔진은 디렉토리까지 prune) 추가
- `packages/machine/tests/test_spawner.py` `TestSpawn`: `test_spawn_sets_codex_home_for_codex_engine`(env 주입 경로 검증, materialize 된 디렉토리 존재 검증), `test_spawn_does_not_set_codex_home_for_other_engines`(3개 엔진 parametrize, ambient `CODEX_HOME` 제거 후 검증) 추가

## Decisions

사용자와의 대화에서 3개 옵션을 비교:

- **A — machine spawner 에서 env 주입** ← 선택. 한 줄 변경으로 끝나고, spawner 가 이미 `.claude/skills` 심볼릭 링크·`CLAUDE.md` 합성 등 per-agent 경로 세팅을 전담하는 패턴과 일관
- **B — agent adapter 에서 `.codex/config.toml` 을 tomllib 로 파싱해 `CodexOptions(config=...)` 로 `--config` CLI override 펼쳐 넣기**: adapter 책임 증가, config 딥이 크면 CLI 라인이 길어짐. 옵션/생성자 API 변화에 더 취약
- **C — adapter 에서 `os.environ["CODEX_HOME"]` 임시 주입을 `secrets_in_env` 스타일 context 로 감싸기**: A 와 결과 동등하지만 agent 프로세스 쪽에서 수행 — 동일 agent 내 codex 이외 경로(없긴 하지만)로 누출 방지를 위한 context 관리 부담

결정 근거: A 가 (1) 단일 변경 지점 (2) 부가 효과로 per-agent codex 상태 격리 (auth.json 등) (3) #142 에서 claude-code `.mcp.json` 재배치할 때와 같은 "spawn 시점에 엔진별 경로 규칙을 맞춘다"는 접근과 정합. adapter 는 SDK 의존이라 SDK 버전 드리프트에 대한 노출면이 더 넓다.

rejected 항목:
- `auth.json` 격리로 인해 호스트 사용자가 미리 `codex auth login` 한 세션을 못 쓰게 되는 것에 대한 걱정 — adapter(`packages/agent/doorae_agent/integrations/codex.py:221-222`)가 이미 `secrets_in_env` 로 `OPENAI_API_KEY`/`OPENAI_BASE_URL` 을 주입하는 구조이므로 auth.json 경로에 의존하지 않음. #197 LLM gateway 흐름과도 정합
- 기존 `_msg` fixture 의 기본 engine 이 `"codex"` 여서 prune 테스트 한 건이 "빈 .codex 도 사라져야 함" 을 주장하고 있었던 점 — 해당 테스트 semantics 를 "codex 엔진은 재생성, 다른 엔진은 prune" 으로 쪼개서 유지

가정 (깨지면 재검토):
- codex app-server 가 `$CODEX_HOME` 을 런타임마다 재해석 (adapter 가 `Codex()` 호출 시점에 env 에 있어야 함) — 현재 `AsyncStdioTransport.start()` 가 `os.environ` 를 그 시점에 스냅샷 → 유효
- codex app-server 가 `.codex/` 에 쓰는 파일들(history, sessions, logs) 이 0o700 디렉토리에서 정상 동작 — 기본 codex 설치가 동일 권한 기대

## Result

- `packages/machine` 271/271 통과 (기존 265 + 신규 6)
- `packages/agent` 241/241 통과 (회귀 없음 확인)
- spawner.py +33 lines, test_materialize.py +57 (일부 기존 테스트 업데이트 포함), test_spawner.py +83
- 다음 agent01-codex 재 spawn 시 `/home/e7217/.doorae/agents/<agent_id>/.codex/config.toml` 의 `[mcp_servers.*]` 가 실제로 codex app-server 에 로드됨. MCP 템플릿 첨부 UI ↔ 런타임 효과가 비로소 일치
- 부가 효과: codex `auth.json`/`history.jsonl`/`sessions/` 가 per-agent 로 격리 — 여러 agent 간 세션 혼선 및 호스트 사용자와의 공용 상태 오염 제거
