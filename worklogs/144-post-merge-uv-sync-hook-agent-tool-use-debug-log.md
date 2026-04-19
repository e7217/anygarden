# chore(repo): post-merge uv sync hook + agent tool-use debug log (#144)

- Commit: `a6cbae2`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #144 (issue)

## Situation

PR #142 진단 과정에서 두 가지 운영 공백이 드러났다. 첫째, main 을 pull해도 `.venv/bin/doorae-agent`가 재생성되지 않아 머신 데몬 spawner가 `shutil.which("doorae-agent")` 조회에 실패하고 `uvx doorae-agent`(PyPI 캐시된 구버전)로 조용히 fallback했다. 그 결과 PR #137의 `permission_mode="bypassPermissions"` 코드가 런타임에 반영되지 않았고 agent01-claude의 MCP 호출 실패 원인 파악에 시간이 크게 늘어났다. 둘째, agent 런타임이 실제로 어떤 툴을 호출했는지 로그에 전혀 남지 않아 매번 `ps -ef | grep server-github`로 프로세스 관측을 해야 MCP 서버 spawn 여부를 확인할 수 있었다.

## Task

- main pull 후 editable install이 자동으로 재생성되도록 git hook 도입 (단, 기존 개발자 워크플로우 강제 변경 없이 opt-in)
- hook이 저장소에 체크인돼 팀 공유되고 실수 여지가 없도록 함
- Claude adapter가 `ToolUseBlock` 수신 시 structlog 엔트리를 찍어 "MCP 실제 호출" 여부를 로그만으로 판별 가능하게
- 로그는 구조 정보(툴 이름 + input keys)만, **값은 절대 기록 안 함** (MCP 툴 인자에 토큰·이메일·repo 경로 등 민감정보가 빈번함)
- 기존 agent 133개 + cluster 575개 테스트 회귀 없음

## Action

- `.githooks/post-merge` 신규 (실행 권한 0755)
  - `uv sync --all-packages` 한 줄 실행 + 로그 한 줄
  - `set -e`, `cd $(git rev-parse --show-toplevel)`로 서브셸 안전성 확보
  - 상세 주석에 도입 배경과 opt-in 이유 명시
- `Makefile`
  - `.PHONY`에 `setup` 추가
  - `setup` target 신규 — `git config core.hooksPath .githooks` 한 번 + 초기 `uv sync --all-packages` + 사용자 안내 에코
- `packages/agent/doorae_agent/integrations/claude_code.py:254-283`
  - `_collect_reply`의 AssistantMessage 루프에서 `type(block).__name__ == "ToolUseBlock"` 분기 추가
  - `logger.info("claude_code.tool_use", tool_name=..., input_keys=[...])`
  - `getattr(block, "input", None) or {}` 로 None-safe 처리
  - 기존 TextBlock 필터링 로직은 `if block_type != "TextBlock": continue`로 변경해 ToolUseBlock 분기 후 흐름 유지
- `packages/agent/tests/test_integrations/test_claude_code.py`
  - `fake_sdk` fixture의 `ToolUseBlock` stub에 `name="mcp__github__get_me"`, `input={"reason": "debug"}` 추가 (real SDK 속성 반영)
  - `test_tool_use_emits_structlog_entry` 신규 — `capfd`로 stdout 캡처 후 `claude_code.tool_use` 라인 존재·`tool_name=mcp__github__get_me` 포함·키 `'reason'` 포함·값 `debug`는 해당 라인에 없음 검증
  - structlog가 stdlib logging이 아니라 stdout 직접 출력이라 `caplog`는 안 잡혀 `capfd` 선택

수동 검증:
- `make setup` 실행 → `.git/config`에 `core.hookspath = .githooks`
- `bash .githooks/post-merge` 직접 실행 → `[post-merge] running: uv sync --all-packages` + uv 출력 정상
- agent 174/174 + cluster 575/575 pass

## Decisions

`.tmp/plan-144-post-merge-hook-and-tool-use-logging.md` 근거:

**hook 배포 방식**
- A. 개별 개발자가 `.git/hooks/post-merge`에 수동 파일 생성 — 실수 여지 큼, 팀 표준 불가능
- B. `.githooks/` 디렉토리 체크인 + `core.hooksPath` 설정 → **선택**. 저장소가 스크립트 소스 오브 트루스, 한 번의 `make setup`으로 활성화
- C. husky 같은 Node 기반 hook 매니저 — Python workspace에 Node dep 도입 과함

결정적 근거: 기존 `Makefile`에 `install` 패턴이 있어 `setup`도 같은 관례를 따르면 학습 비용 없음. `.githooks/`는 일반 디렉토리라 diff 가독성 좋고 PR 리뷰 가능.

**opt-in vs 강제 적용**
- A. `post-checkout` 등 더 많은 hook을 추가해 강제화 — 기존 CI·외부 사용자 워크플로우 깨뜨릴 수 있음
- B. `.githooks/`는 포함하되 `core.hooksPath`는 각자 선택 → **선택**. 체크인된 파일이라 역할만 명확하면 대부분 `make setup` 돌림. 일부 환경은 자체 hook이 있으므로 강제하지 않음

**input 값 로깅 여부**
- A. 툴 이름만 로깅 — 키 구조를 모르면 "왜 그 툴을 호출했는지" 추론 어려움
- B. tool name + input **keys** → **선택**. 값은 제외
- C. tool name + 전체 input (필드별 마스킹) — MCP 서버마다 스키마가 달라 일괄 마스킹 규칙 유지 어려움

결정적 근거: 오늘 진단 케이스에서 필요했던 정보는 "`mcp__github__get_me`가 불렸는가"와 "input 구조가 기대대로인가" 두 가지뿐이었음. `{"reason": "debug"}`에서 `"debug"`는 로그 볼 필요 없고, PII/credential 누수 위험만 있음.

**로그 레벨: info vs debug**
- A. `logger.debug` — 기본적으로 off라 production 진단에 도움 안 됨
- B. `logger.info` → **선택**. 툴 호출 빈도가 turn당 몇 건이라 볼륨 부담 미미하면서 default 레벨에서 바로 보임

**테스트 capture 방식: caplog vs capfd**
- structlog는 설정에 따라 stdlib logging에 연동되기도 하지만 이 프로젝트는 stdout 직접 출력. `caplog`는 stdlib 레코드만 수집해서 못 잡음. `capfd`로 문자 수준 검증 → 안정적

가정: MCP 툴의 input 필드명 자체는 민감정보가 아니다 (예: `query`, `repo`, `path`). 만약 MCP 서버가 필드명에도 토큰을 넣는 비정상 스키마가 등장하면 이 전제 재검토 필요.

가정: `uv sync --all-packages`는 uv가 정상 설치된 환경에서 빠르고 idempotent하다. offline 환경에서는 hook이 실패할 수 있지만 이 경우 일반 개발 워크플로우 자체가 깨진 상태이므로 hook만 문제 아님.

## Result

- `make setup` 실행 시 `.githooks/post-merge`가 활성화돼 `git pull` 할 때마다 `uv sync --all-packages` 자동 실행. `.venv/bin/*` stale 재발 방지
- agent가 MCP 툴을 호출할 때 cluster/machine 로그에 `claude_code.tool_use tool_name=mcp__github__get_me input_keys=['reason']` 형태의 structlog 엔트리 출력
- agent 테스트 174/174 통과 (기존 173 + 신규 1), cluster 575/575 유지
- hook이 체크인돼 신규 개발자가 단순히 `make setup` 한 번으로 동일 환경 구축
- 후속 가능 과제: 동일 패턴으로 cluster/frontend용 hook 추가 (예: schema migration alert), 로그 샘플링 (turn 당 수십 툴 호출 시 볼륨 관리)
