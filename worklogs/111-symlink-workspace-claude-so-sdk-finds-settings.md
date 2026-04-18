# fix(machine): symlink workspace/.claude → ../.claude so SDK finds settings (#111)

- Commit: `824c960` (824c9605c70eee5a76b8a96aae8aa07a76de31d8)
- Author: Changyong Um
- Date: 2026-04-18T18:20:XX+09:00
- PR: #111

## Situation

직전 커밋 `9154a66`이 claude-code 에이전트의 권한 문제를 풀었다고 선언했지만, 실제로는 동작하지 않았다. 계획 문서(`.tmp/plan-111-claude-code-default-settings.md`) Phase 6.1에서 "가정 1"로 명시했던 부분 — claude-agent-sdk가 cwd=`workspace/`에서 한 단계 위 `agent_root/.claude/settings.json`을 walk-up으로 찾을 것이다 — 이 검증되지 않은 채 머지될 뻔했다. 사용자가 워크트리에서 수동 검증을 요청해 본격적으로 살펴본 결과 가정이 깨진 것을 확인.

## Task

- claude-agent-sdk가 settings.json을 어디서 찾는지 정확히 파악
- 만약 cwd 바로 밑만 본다면, spawner가 `workspace/.claude` 슬롯에 카노니컬 `agent_root/.claude/`를 노출시키는 브리지를 추가
- 다른 엔진은 영향받지 않게
- end-to-end 검증으로 SDK 권한 grant 확인 후 머지 가능 상태로 만들기

## Action

- 검증: `claude --debug-file <log>` 으로 settings 디스커버리 동작을 직접 관찰. 결정적 로그:
  - 브리지 없음: `Broken symlink or missing file encountered for settings.json at path: <workspace>/.claude/settings.json` → settings.json 미인식
  - 브리지 있음 (`workspace/.claude → ../.claude`): `Applying permission update: Adding 10 allow rule(s) to destination 'projectSettings': ["WebSearch","WebFetch","Bash","Read","Write","Edit","Glob","Grep","Agent","TodoWrite"]` → 디폴트 10개 도구 모두 grant
- `packages/machine/doorae_machine/spawner.py:520-538` — `_materialize_agent_dir()` 의 workspace AGENTS.md/CLAUDE.md 심볼릭 블록 직후, return 직전에 `if msg.engine == "claude-code"` 분기 추가. 기존 `workspace/.claude` 슬롯이 있으면(symlink/file/dir 어느 쪽이든) 정리하고 `../.claude` 로 심볼릭 링크 작성
- `packages/machine/tests/test_materialize.py` — `TestClaudeCodeDefaultSettings` 에 두 테스트 추가:
  - `test_workspace_claude_symlink_for_claude_code` — 링크 존재, 타깃이 `../.claude`, 링크 통해 settings.json 도달 가능
  - `test_no_workspace_claude_symlink_for_other_engines` — codex/gemini-cli/openhands는 링크 안 만듦 (parametrize)
- 머신 패키지 회귀 225 pass (이전 221 → 신규 4 추가)

## Decisions

검증 단계에서 가정 1이 깨진 직후, 세 가지 경로를 따져봤다:

- **A. 디렉토리 전체를 심볼릭 링크 (`workspace/.claude → ../.claude`)** — 채택. 한 줄로 `.claude/settings.json` 뿐 아니라 `.claude/skills/`, 미래의 `.claude/<무엇>` 까지 자동 노출. 기존 spawner의 `agent_root/.claude/skills → ../skills` 와도 자연스럽게 호환 (간접 참조 한 번 더 거쳐도 결국 같은 곳으로 resolve됨)
- **B. 파일 단위 심볼릭 링크 (`workspace/.claude/settings.json → ../../.claude/settings.json`)** — 기각. 새 파일이 `.claude/` 에 추가될 때마다 spawner를 고쳐야 함. settings.json 외의 future artifact (예: settings.local.json — 이미 SDK가 찾고 있음) 마다 똑같은 brittle 패치 반복
- **C. cwd 자체를 `agent_root` 로 변경** — 기각. 어댑터(`claude_code.py:_build_options()`)가 `Path.cwd()` 를 SDK 에 넘기므로 어댑터 변경이 필요하고, 더 중요하게는 `workspace/` 가 sandbox 경계 역할을 하는 isolation 모델이 무너짐. agent 가 instruction 파일이나 settings 자체를 직접 쓸 수 있게 됨

결정적 근거: A는 isolation contract와 정확히 같은 패턴(`workspace/AGENTS.md → ../AGENTS.md`)이라 신뢰 모델 일관성이 보장된다. claude-code SDK 측 sandbox가 심볼릭 링크 대상이 sandbox 밖(`agent_root/.claude/`)이라는 사실을 알아채면 write 시도를 거부하므로, agent 가 자기 권한 파일을 늘리는 prompt-injection 류 공격도 막힌다.

가정 (재검토 트리거): claude SDK는 `.claude/` 디렉토리 자체가 심볼릭 링크여도 settings.json 발견에 문제없다 — 검증 로그에서 직접 확인했지만, SDK 메이저 업그레이드 시 link-following 동작이 바뀌면 깨질 수 있음. 디버그 로그가 정확히 `<workspace>/.claude/settings.json` 경로로 나오므로 watch 항목이 변하면 즉시 감지 가능.

## Result

- 머신 패키지 신규 테스트 12개 모두 pass (8 → 12), 회귀 225 pass
- 실제 spawner 가 만든 디렉토리 구조에서 SDK 가 settings.json 을 정확히 인식하고 10개 도구 권한이 grant 되는 것을 end-to-end debug 로그로 확인
- claude-code 에이전트가 새로 spawn 되면 즉시 WebSearch/WebFetch/Bash 등 표준 도구 사용 가능 — issue #111 의 원래 증상("죄송합니다. 웹 검색 도구에 대한 권한이 아직 승인되지 않아…") 완전 해소
- 미해결: SDK가 `.claude/` 심볼릭 링크 따라가는 동작이 향후 SDK 메이저 업데이트에서 깨지지 않는지 — pyproject.toml 의 `claude-agent-sdk` 핀 업그레이드 시 본 worklog 와 함께 issue #111 회귀 테스트를 한 번 돌려볼 것
