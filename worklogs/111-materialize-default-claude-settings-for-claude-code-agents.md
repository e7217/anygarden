# feat(machine): materialize default .claude/settings.json for claude-code agents (#111)

- Commit: `9154a66` (9154a6639ac26678903b88f8279d1f290aaddd4f)
- Author: Changyong Um
- Date: 2026-04-18T18:12:09+09:00
- PR: #111

## Situation

claude-code 엔진을 쓰는 새 에이전트에게 WebSearch가 필요한 질문(예: "오늘 세종시 날씨")을 던지면 "권한이 승인되지 않아 실시간 날씨 정보를 조회할 수 없다"는 응답이 돌아왔다. 원인은 두 가지가 겹친 결과였다. 어댑터(`claude_code.py:_build_options()`)는 `setting_sources=["project"]`만 지정하고 `permission_mode`/`allowed_tools`는 비워두는데, spawner는 admin manifest에 `.claude/settings.json`이 없으면 그 슬롯을 비워둔다. 사람이 없는 headless 환경에서 SDK 기본 ask 모드는 모든 도구 호출을 거부하므로 에이전트는 사실상 텍스트 챗봇으로 퇴화한다. gemini-cli는 어댑터에서 `--approval-mode yolo`로 우회하고 codex는 `workspace-write` sandbox로 trust 모델을 명시하지만, claude-code엔 동등한 안전망이 없었다.

## Task

- spawner가 claude-code 엔진을 만날 때 `.claude/settings.json` 디폴트 템플릿을 자동 materialize 하기
- admin manifest로 같은 경로의 파일이 들어오면 admin 버전이 우선하는 override 경로 유지하기
- prune이 매 spawn마다 `.claude/`를 통째로 삭제해도 결정론적으로 다시 작성되는 동작 보장하기
- 다른 엔진(codex, gemini-cli, openhands)에는 `.claude/settings.json`이 만들어지지 않게 하기

## Action

- `packages/machine/doorae_machine/spawner.py`
  - 모듈 상수 `_CLAUDE_CODE_DEFAULT_SETTINGS` 추가 (line 134-167). `permissions.allow`에 WebSearch, WebFetch, Bash, Read, Write, Edit, Glob, Grep, Task, TodoWrite 10개 표준 도구 화이트리스트
  - `_materialize_agent_dir()`의 engine_secrets `.env` 블록 직전에 디폴트 작성 분기 추가 (line 386-399). `msg.engine == "claude-code"` 이고 슬롯이 비어 있을 때만 작성, chmod 0o600
- `packages/machine/tests/test_materialize.py`
  - 신규 클래스 `TestClaudeCodeDefaultSettings` 추가, 테스트 6개 (parametrize 펴면 8개)
  - default 작성 / WebSearch 포함 / chmod 600 / 다른 엔진 미작성(codex·gemini-cli·openhands) / admin override / re-spawn 복원

## Decisions

`.tmp/plan-111-claude-code-default-settings.md`의 Phase 3.2에서 세 갈래 결정을 정리했다.

**어댑터 vs spawner 위치**: 어댑터에서 `permission_mode="bypassPermissions"`를 박는 안과 spawner에서 디스크 디폴트를 까는 안을 비교. bypass는 `spawner.py:407-419`에 명시된 "sandbox + 명시적 grant" 신뢰 모델과 정면 충돌하고 admin per-agent 분리도 막아서 기각. spawner 안은 gemini가 이미 `.gemini/settings.json`을 spawner 흐름에서 다루는 패턴과 대칭이고, 디스크에 파일로 박혀 디버그·감사가 쉽다.

**매핑 딕셔너리 vs if 분기**: `_ENGINE_DEFAULT_FILES` 매핑으로 데이터 구조화하는 안과 단순 if 분기 비교. 지금은 claude-code 한 엔진뿐이라 매핑은 YAGNI. 기존 `use_real_copy = msg.engine == "gemini-cli"` (line 451) 패턴과 일관되게 if 분기 채택. 두 번째 엔진이 같은 패턴을 필요로 하면 그때 매핑으로 리팩터.

**admin manifest 머지 전략**: 디폴트와 admin을 JSON deep merge하는 안 vs admin 전체 대체 안 vs SDK 자체 머지에 위임 비교. JSON 머지는 admin이 머지 의미론을 학습해야 하고 deny와의 일관성이 어려움. SDK 위임은 `setting_sources=["project"]`로 의도적으로 user-level을 차단해둔 격리 정책을 무너뜨림. "admin manifest는 이 에이전트의 settings.json 전체"라는 단순 대체가 가장 예측 가능. 구현은 manifest 파일 작성 루프 다음에 디폴트 작성 분기를 두고 `if not exists`로 자연스럽게 admin 우선이 되도록 했다.

가정: claude-agent-sdk가 `setting_sources=["project"]` + cwd=`workspace/` 조합에서 한 단계 위(`agent_root/.claude/settings.json`)의 settings.json을 찾는다는 점. SDK가 cwd만 보고 walk-up을 안 하면 `workspace/.claude → ../.claude` 심볼릭 링크 추가가 필요해진다 (재검토 트리거). 1차 구현은 가정대로 가고 수동 검증에서 확인하기로 했다.

## Result

- 신규 테스트 8개 모두 pass, 머신 패키지 전체 회귀 221 pass (디폴트 도입 전 35 → 도입 후 43 materialize 테스트), cluster 패키지 406 pass
- claude-code 엔진 에이전트를 새로 spawn하면 `~/.doorae/agents/<id>/.claude/settings.json`이 자동으로 깔리고 WebSearch/WebFetch가 즉시 동작 가능
- 권한을 좁히고 싶은 admin은 manifest로 자기 settings.json (예: `{"permissions": {"allow": ["Read", "Glob"]}}`)을 보내면 디폴트 대신 admin 버전이 단일 진실 원천이 됨
- 기존 머신에서 이미 spawn된 에이전트 디렉토리는 다음 spawn 사이클의 prune+rematerialize에서 자동 흡수 — 별도 마이그레이션 불필요
- 미해결: SDK가 cwd 한 단계 위의 `.claude/settings.json`을 실제로 로드하는지 end-to-end 수동 검증은 PR 머지 전 별도 확인 단계로 남음. 못 찾을 경우 `workspace/.claude → ../.claude` 심볼릭 링크 추가 패치 필요
