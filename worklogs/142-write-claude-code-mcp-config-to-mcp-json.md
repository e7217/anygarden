# fix(cluster): write claude-code MCP config to .mcp.json (#142)

- Commit: `b972b0f`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #142 (issue)

## Situation

doorae는 attach된 MCP 템플릿을 agent workspace의 `.claude/settings.json`의 `mcpServers` 섹션에 병합해 써왔다. 하지만 **Claude Code 2.x는 이 파일의 `mcpServers` 섹션을 읽지 않는다**. 2.x는 project-local MCP 설정을 **workspace 루트의 `.mcp.json`**에서 읽도록 구조가 바뀌었고, `.claude/settings.json`은 `permissions`·`sandbox` 같은 non-MCP 설정 전용이 됐다. 결과: admin UI에서 GitHub MCP를 attach해도 Claude 서브프로세스는 해당 MCP가 없는 것처럼 동작했고, agent는 "GitHub MCP가 연결되어 있지 않다"고 응답했다. #134 (MCP auto-approve)와 #138 (Fernet 키 영속)이 각각 권한 게이트와 키 회전 문제를 해결했지만 애초에 툴 자체가 Claude에 노출되지 않고 있어서 두 수정의 효과가 드러나지 않았다.

## Task

- claude-code의 MCP 설정 경로를 `.mcp.json`(workspace 루트)으로 이동
- AgentFile validator가 루트 레벨 `.mcp.json`을 허용하도록 확장 (prefix-only 체계에 exact-match 엔트리 추가)
- cluster / machine 양쪽 whitelist를 lockstep으로 유지
- gemini-cli (`.gemini/settings.json`), codex (`.codex/config.toml`) 경로는 변경하지 않음 — 실험으로 현재 경로가 유효함 확인
- 기존 `merge_for_engine` JSON merge 로직 재사용 (`.mcp.json`과 기존 `.claude/settings.json`이 동일한 `{mcpServers: {...}}` 스키마)
- 기존 cluster 572 + machine 232 테스트 회귀 없음

## Action

- `packages/cluster/doorae/mcp_templates/merge.py`
  - `CLAUDE_SETTINGS_PATH` 상수를 `.claude/settings.json` → `.mcp.json`으로 변경
  - 모듈 docstring에 #142 경위 주석 추가
- `packages/cluster/doorae/mcp_templates/__init__.py`
  - 패키지 docstring 경로 레퍼런스 갱신
- `packages/cluster/doorae/agent_files.py`
  - `_ALLOWED_EXACT_PATHS: frozenset[str] = frozenset({".mcp.json"})` 신규 추가
  - `validate_agent_file_path`가 prefix 매치 실패 시 exact-match 테이블도 확인하도록 분기 확장. 에러 메시지에 양쪽 정보 포함
- `packages/machine/doorae_machine/agent_dir.py`
  - 위 두 변경 동일 반영 (server-machine lockstep 컨벤션)
- `packages/cluster/tests/test_mcp_templates_merge.py`
  - `TestSettingsPath.test_maps_engines_to_paths`에서 claude-code path assertion을 `.mcp.json`으로 교체, 주석에 #142 배경 기록
- `packages/cluster/tests/test_agent_files_validation.py`
  - allowed-paths parametrize에 `.mcp.json` 추가
  - rejected-paths에 `nested/.mcp.json`, `.mcp.json.bak` 추가 (exact-match이므로 prefix-variant는 거부되어야 함을 pin)
- `packages/cluster/tests/test_mcp_templates_lifecycle.py`
  - 세 assertion 모두 `.claude/settings.json` → `.mcp.json`로 교체
  - admin-override 시나리오의 AgentFile path를 `.mcp.json`으로 이동하고 `permissions.allow` 대신 `custom_key` 보존 검증으로 단순화 — non-mcp 키 보존 의도만 유지. 주석으로 "permissions는 .claude/settings.json에 그대로 남고, MCP는 별도 파일로 분리됨" 명시

**진단 증거 체인** (PR body에도 기록):
1. agent workspace `.claude/settings.json`의 `mcpServers.github`에 토큰 치환 완료 상태 확인
2. 메시지 처리 중 `ps` 관측 — `claude` CLI는 뜨지만 `@modelcontextprotocol/server-github` 자식은 전혀 spawn 안 됨
3. agent cwd에서 `claude --setting-sources=project --print "List MCP servers"` 실행 → `github` 없음, user scope claude.ai MCP들만 표시
4. `cp .claude/settings.json .mcp.json` 후 같은 명령 재실행 → `github` 즉시 등장. 이 한 단계로 원인 확정

## Decisions

`.tmp/plan-142-mcp-config-path-mcp-json.md` 근거:

**경로를 `.mcp.json`으로 이동 vs `.claude/settings.json` 아래에 유지**
- A. `.claude/settings.json` 스키마 변경 — 기대/실망: Claude Code 2.x가 이미 해당 섹션을 무시하므로 해결책 없음
- B. `.mcp.json`으로 파일 이동 → **선택**. Claude Code 2.x 공식 project-local MCP 경로. merge 함수 재사용 가능
- C. `~/.claude.json`의 `projects[<cwd>].mcpServers` 직접 편집 — HOME 전역 파일을 agent 간섭 없이 수정하려면 파일 락/atomic write 필요. 복잡도 과다

결정적 근거: 실험으로 `.mcp.json`이 즉시 인식되는 것을 확인했고, 기존 JSON merge 로직을 그대로 쓸 수 있음. 파일 하나의 이름만 바꾸는 최소 변경으로 해결.

**validator 확장 전략**
- A. `_ALLOWED_PREFIXES`에 `".mcp.json"` 추가 — prefix 매칭이라 `.mcp.json.bak`, `.mcp.json.old` 같은 파일도 통과해버림
- B. 별도 `_ALLOWED_EXACT_PATHS` 테이블 → **선택**. 의미 명확, 오탐 방지, 향후 유사 루트 파일 추가 시 패턴 재사용

결정적 근거: `.mcp.json`은 워크스페이스 루트의 "정확한 파일명" 규약이라 prefix 매칭과 의미가 다름. exact 리스트로 분리하면 추후 `.gitignore`, `README.md` 등을 단일 경로로 허용하고 싶을 때도 같은 메커니즘을 쓸 수 있다.

**다른 엔진은 건드리지 않음**
- gemini-cli: 내 유저 `~/.gemini/settings.json`에 `notionApi` MCP가 정상 동작 확인 → 현재 경로 유효
- codex: `codex-python` SDK는 `ThreadStartOptions`로 bypass 플래그를 전달하는 방식이라 (#134 참고) settings 파일 포맷이 MCP 로딩에 직접 관여하지 않음. 별도 이슈로 검증 필요하면 그때 처리

결정적 근거: "모든 엔진을 한 번에 고쳐야 한다" vs "실증 가능한 것만 수정"에서 후자가 안전. 공식 문서 확신 없는 상태에서 working path를 바꾸면 회귀 가능.

**admin override 시나리오 재정의**
- 기존 테스트는 admin이 `.claude/settings.json`에 `permissions.allow`와 `mcpServers`를 같이 넣고 두 섹션 모두 preserve되는 것을 검증
- 이번 변경 후: `permissions`는 `.claude/settings.json`에 그대로 남음 (Claude Code 2.x가 해당 섹션은 읽음), `mcpServers`는 `.mcp.json`으로 이동 → 두 파일 분리
- 테스트는 MCP merge 로직만 검증하므로 admin override base를 `.mcp.json`으로 옮기고 non-mcp 키 보존만 pin

가정: Claude Code 2.x와 호환만 맞추면 됨 (1.x 구버전 하위 호환은 스코프 외). 2026-04-19 현재 doorae가 요구하는 claude CLI 2.1+가 `.mcp.json`을 지원한다는 실증 있음.

가정: gemini-cli 0.37 / codex app-server 최신이 현재 경로로 정상 동작한다는 실험 근거. 향후 버전 업데이트로 경로가 바뀌면 동일 패턴으로 이동 가능.

## Result

- agent workspace에 `.mcp.json`이 생성되고 Claude CLI의 `mcp list`에서 attach된 MCP가 노출됨 (agent01-claude + GitHub 템플릿에서 수동 검증 예정)
- cluster 575/575 (572 기존 + 3 시나리오 조정/추가) 테스트 통과
- machine 232/232 테스트 통과 (whitelist 확장만)
- `.claude/settings.json` legacy 경로에 admin이 넣은 non-MCP 설정(permissions 등)은 그대로 유효 — 파일이 분리됐을 뿐
- 기존 attach된 MCP instance들은 DB 그대로 유지; 다음 spawn에서 `.mcp.json`으로 새 경로에 write됨. admin UI에서 추가 조작 불필요
- 후속 과제:
  - user-scope `~/.claude.json`의 claude.ai 원격 MCP들이 `--setting-sources=project` 플래그와 무관하게 agent subprocess에도 노출되는 격리 문제는 별건 (#142 스코프 외)
  - codex의 MCP 설정 경로가 실제로 spawn에 영향을 주는지 verification은 향후 검증
