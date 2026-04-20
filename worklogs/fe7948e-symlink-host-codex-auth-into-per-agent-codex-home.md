# fix(machine): symlink host ~/.codex/auth.json into per-agent CODEX_HOME

- Commit: `fe7948e`
- Author: Changyong Um
- Date: 2026-04-20
- PR: —

## Situation

PR #213 (`cb937c2`) 이 codex 엔진 에이전트에 대해 `.codex/*` 오버레이가 있을 때 `CODEX_HOME` 을 per-agent `.codex/` 로 리다이렉트하게 해서 MCP 템플릿이 실제로 로드되도록 수정했다. 리뷰 스탑훅 피드백을 받아 "오버레이 있을 때만" 스코핑까지 적용했음.

하지만 머지 후 실제 운영 환경(agent01-codex, ID `0b4a63f4-2403-4e5c-a4ba-9804c17980db`)에서 응답이 끊어짐. 조사 결과:

- agent 에 MCP 템플릿(github) 이 첨부돼 있음 → `.codex/config.toml` 오버레이 존재 → `CODEX_HOME` 리다이렉트 발동
- per-agent `.codex/` 에는 `auth.json` 이 없음
- host `~/.codex/auth.json` 에는 ChatGPT OAuth 토큰이 들어있음 (`codex auth login` 으로 로그인한 상태)
- `engine_secrets` 에 `OPENAI_API_KEY` 없음 (이 배포는 ChatGPT 로그인 기반 auth 사용)
- → codex app-server 가 auth 를 못 찾음 → task_start 후 8초 만에 `task_complete` with `last_agent_message: None` (빈 응답)

실측 검증: `ln -sf ~/.codex/auth.json <agent_root>/.codex/auth.json` 으로 수동 symlink 만들고 agent 재기동 후 ping 테스트 → 정상 pong 응답. 즉 auth.json 만 다리 놓아주면 해결.

이게 정확히 PR #213 리뷰 스탑훅이 "unconditional CODEX_HOME redirection regresses supported host-auth codex startup" 으로 경고했던 시나리오의 두 번째 층이다. 스코핑(오버레이 있을 때만 리다이렉트) 만으로는 충분치 않았음 — 오버레이 있는 동시에 host-auth 에 의존하는 케이스가 실제 배포에 존재.

## Task

- `.codex/*` 오버레이가 있어 `CODEX_HOME` 리다이렉트가 발동할 때, per-agent `.codex/auth.json` 을 host `~/.codex/auth.json` 로 symlink
- host 에 auth.json 이 없는 fresh 설치에서는 dead link 를 만들지 말 것 (codex 가 더 혼란스러운 에러를 냄)
- admin 이 manifest 로 `.codex/auth.json` 을 직접 제공한 경우 (예: 서비스 계정 토큰) 그 파일이 우선
- non-codex 엔진에는 생성 금지
- prune-and-respawn 사이클 통과 (두 번째 spawn 에도 symlink 재생성)

## Action

- `packages/machine/doorae_machine/spawner.py` `_materialize_agent_dir`: claude-code 기본 settings 블록 뒤에 새 블록 추가. `has_codex_overlay = any(p.startswith(".codex/") for p in msg.files)` 로 stuck 조건 재계산 (spawn 쪽과 동일 조건), `host_auth.is_file() and not per_agent_auth.is_symlink() and not per_agent_auth.exists()` 로 guard 한 뒤 `per_agent_auth.symlink_to(host_auth)` 한 줄. 코멘트로 사용 시나리오 + admin override + 멀티에이전트 공유 토큰 트레이드오프 명시
- `packages/machine/tests/test_materialize.py` 새 `TestCodexHostAuthSymlink` 클래스 — 8개 케이스:
  - `fake_host_codex` fixture: `monkeypatch.setenv("HOME", ...)` 로 `Path.home()` 을 tmp_path 로 돌려 실제 ~/.codex 를 건드리지 않음
  - `test_symlink_created_when_codex_overlay_present` — 행복 경로
  - `test_no_symlink_when_host_auth_missing` — fresh 호스트
  - `test_no_symlink_when_no_codex_overlay` — 오버레이 없는 codex 에이전트 (host-auth 경로 무손상 보존)
  - `test_admin_authored_auth_preserved` — manifest 우선
  - `test_non_codex_engines_do_not_get_auth_symlink` (claude-code/gemini-cli/openhands parametrize)
  - `test_stale_auth_symlink_refreshed_across_spawns` — prune 후 재생성

## Decisions

대안 비교:

- **B1 — auth.json 심볼릭 링크 (선택)**: 약 10줄의 materialize 추가. pre-#213 auth 동작 복원 + per-agent 세션/history 격리 유지 (#213 의 부가 효과) + per-agent MCP 설정 유지. 호환성과 단순성 최적.
- **B2 — CODEX_HOME 리다이렉트 철회 + adapter 가 `CodexOptions(config=...)` 로 MCP 주입**: 구조적으로 더 깔끔하나 #213 리버트 + adapter 로직 추가 + `.codex/config.toml` 파싱. 그리고 결정적 다운사이드: codex 세션/history/logs 가 다시 host `~/.codex/sessions/` 에 공유됨 → 여러 에이전트가 rollout 히스토리를 서로 오염.
- **B3 — auth.json 복사**: rotate 시 stale. symlink 가 follow 시 정확한 최신 토큰을 읽음.

결정적 근거: B1 은 기존 배포의 "MCP + host auth 로그인" 이중 요구를 정확히 충족. 트레이드오프(여러 에이전트가 host 토큰을 공유) 는 pre-#213 에도 동일했으므로 신규 regression 없음.

rejected 항목:
- "symlink 만으로 부족하니 adapter 에서 `--config` CLI override 로 MCP 를 밀어넣자" — adapter 가 per-agent `.codex/config.toml` 을 파싱해야 하는 로직이 생기고, 세션 격리도 잃음
- "engine_secrets 에 OPENAI_API_KEY 를 요구하는 운영 가이드만 업데이트" — ChatGPT 로그인 기반 운영을 강제로 API key 기반으로 전환시키는 큰 UX 변경. 이 PR 의 스코프를 한참 벗어남

가정 (깨지면 재검토):
- host `~/.codex/auth.json` 이 코덱스 CLI 가 로그인 시 생성하는 파일 레이아웃과 동일 (모니터링: codex CLI 가 auth 파일 이름/경로를 바꾸면 symlink 가 stale)
- codex app-server 가 auth.json 을 매번 다시 읽는다는 가정 — 캐시한다면 호스트 재로그인(token rotate) 후 agent 재기동 필요 (pre-#213 에도 동일했던 제약)
- multi-agent 동시 실행 시 auth token refresh race 는 codex 내부 mutex 로 처리된다는 가정 (pre-#213 상태와 동일)

## Result

- `packages/machine` 276/276 통과 (기존 268 + 신규 8)
- 운영 효과 매트릭스:
  - **MCP 첨부 + host-auth 의존** (agent01-codex 케이스): 이전 "빈 응답으로 stuck" → 정상 `pong` ✓
  - **MCP 없음, host-auth**: 변경 없음 (PR #213 스코핑이 이 케이스는 아예 리다이렉트 안 시킴) ✓
  - **MCP 첨부 + `engine_secrets` 기반 auth**: symlink guard 가 `host_auth.is_file()` 통과 시 동작하지만 codex 가 env 의 `OPENAI_API_KEY` 를 더 우선시함 (codex auth precedence) → 무해 ✓
  - **admin 이 `.codex/auth.json` 명시**: manifest 우선, symlink 스킵 ✓
- 남은 후속 검토(별도 이슈):
  - host auth 위치 하드코딩 (`~/.codex/auth.json`) — `CODEX_HOME` 체인이 복잡한 운영 환경에서 재검토 필요 가능
  - admin UI 에서 codex 에이전트의 auth 출처가 "host shared" vs "per-agent" 인지 시각화하면 UX 명확
