# feat(agent): auto-approve MCP tool calls for all engines (#134)

- Commit: `8fa767a`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #134 (issue)

## Situation

MCP 서버를 agent에 attach해도 실제 툴 호출이 권한 게이트에서 막혀 "Claude가 GitHub에 접속할 권한이 없다"고 응답하는 증상이 발생했다. 조사 결과 `.claude/settings.json`에 `mcpServers.github` 항목과 GitHub Personal Access Token이 모두 올바르게 치환돼 있고 토큰 자체도 유효했지만, claude-agent-sdk가 비인터랙티브 세션에서 MCP 툴 호출마다 사용자 승인 프롬프트를 요구하는데 헤드리스 agent 환경에선 응답자가 없어 모든 호출이 거부됐다. 반면 gemini-cli adapter는 이미 `--approval-mode yolo`로 이 문제를 회피 중이었고 codex adapter는 `ThreadStartOptions`를 전달하지 않아 기본 approval 정책에 걸려 있었다.

## Task

- claude-code·codex adapter에서 헤드리스 환경에 맞는 자동 승인 플래그 도입
- 각 엔진 SDK가 제공하는 표준 bypass 경로 사용 (settings 파일 주입이 아닌 런타임 플래그)
- admin이 `.claude/settings.json` / `.codex/config.toml`을 직접 편집한 경우 충돌하지 않도록 merge.py는 건드리지 않음
- gemini-cli는 현 상태 유지 (회귀 검증만)
- 기존 테스트(133건) 회귀 없이 bypass 동작 단위 테스트 추가
- 사용자 의도 "에이전트 권한을 다 푸는 것 (--yolo, allow permission bypass)" 충족

## Action

- `packages/agent/doorae_agent/integrations/claude_code.py:121-151`
  - `_build_options()` kwargs에 `permission_mode="bypassPermissions"` 추가
  - docstring에 결정 근거 (gemini-cli / codex와 동일한 trust model) 기록
- `packages/agent/doorae_agent/integrations/codex.py`
  - `CodexAdapter.__init__`에 `_thread_options_cls: Any = None` 필드 추가 (SDK 부재 시 degrade 지원)
  - `start()`에서 `from codex.options import ThreadStartOptions`를 함께 import하고 `_thread_options_cls`에 저장 — 테스트가 `codex` 모듈을 MagicMock으로 스텁할 때 `codex.options` 서브모듈을 별도로 스텁할 수 있게
  - `on_message()`의 thread 생성부를 `start_thread(options=ThreadStartOptions(approval_policy="never", sandbox=self._sandbox))` 호출로 변경. `_thread_options_cls`가 None이면 기존 시그니처로 폴백
  - 로그 필드에 `approval_policy`, `sandbox` 추가
- `packages/agent/tests/test_integrations/test_claude_code.py:106-141`
  - 기존 `test_passes_cwd_and_setting_sources`에 `opts["permission_mode"] == "bypassPermissions"` 단정 추가
- `packages/agent/tests/test_integrations/test_codex.py` (전체 재작성)
  - `_make_fake_codex_module()`이 `(fake_mod, options_mod, mock_codex, mock_thread)` 4-tuple 반환
  - `FakeThreadStartOptions`가 kwargs를 속성으로 복사하는 stub 제공
  - `_patch_codex(fake_mod, options_mod)` 헬퍼로 `sys.modules`에 `codex`와 `codex.options` 동시 patch
  - 신규 `test_start_thread_passes_bypass_options` — `start_thread` 호출 시 `approval_policy="never"`와 `sandbox="workspace-write"`가 options에 포함되는지 검증 (args/kwargs 모두 수용)
  - 기존 9개 테스트 모두 새 unpacking 패턴으로 업데이트

## Decisions

`.tmp/plan-134-mcp-auto-approve-all-engines.md`에서 결정:

**bypass를 어디에 꽂을까**
- A. SDK 옵션 (`permission_mode` / `ThreadStartOptions.approval_policy`) → **선택**
- B. settings 파일 (`.claude/settings.json`의 `permissions.allow=["*"]`, codex config의 `approval_policy="never"`)에 merge 레이어가 삽입
- C. `canUseTool` 콜백으로 항상 allow 반환

결정적 근거: 
- B는 admin이 같은 파일을 편집하면 충돌 (예: admin이 `permissions.allow=["Read"]`로 제한을 둔 경우 우리 merge가 그걸 덮어쓰면 의도 위반). SDK 레이어 플래그는 admin 설정과 독립.
- C는 단순 "모두 허용" 요구에는 과함. 세밀한 정책은 후속 이슈로 미룸.
- A는 gemini-cli가 이미 쓰는 `--approval-mode yolo`와 동등한 트러스트 모델이라 세 엔진이 같은 층에서 일관.

**codex ThreadStartOptions import 위치**
- A. 모듈 최상위 import → 테스트가 `codex` 전체를 MagicMock으로 patch할 때 서브모듈 import 실패
- B. `on_message` 호출 때마다 inline import → 매 호출 오버헤드 + 테스트 복잡도 동일
- C. `start()`에서 한 번 import해 instance attribute로 저장 → **선택**

결정적 근거: C는 SDK 로딩이 start() 시점 1회로 한정된다는 기존 컨벤션과 일치하고, 테스트에서 `codex.options` 서브모듈을 별도로 stub할 수 있게 하는 가장 간단한 방법. SDK 미설치 시 `_thread_options_cls`가 None으로 남아 자연스럽게 degrade.

**AskForApproval 값 중 `"never"` 선택**
- `"untrusted"`, `"on-failure"`, `"on-request"`, `"never"` 중 사용자 요구 "yolo"에 가장 직접적으로 대응하는 `"never"`를 선택. 중간값들은 부분 승인이라 헤드리스 환경엔 부적합.

**sandbox 유지 (`"workspace-write"`)**
- 완전 bypass라고 해도 agent workspace 내부로 쓰기를 제한하는 기본 sandbox는 유지. 호스트 파일시스템 접근 차단은 여전히 유효하므로 agent 자체의 프로세스 격리는 보존.

가정: claude-agent-sdk와 codex-python은 각 리터럴 타입(`PermissionMode`, `AskForApproval`)을 안정적으로 지원한다. SDK 메이저 업그레이드로 리터럴 이름이 바뀌면 타입체커가 잡아줌. 런타임 실패가 아니라 mypy/pyright 단계에서 드러나게 됨.

가정: admin이 `--approval-mode interactive` 같은 역제한을 원할 경우 현재는 방법이 없음. 후속 과제로 per-agent `permission_mode` 컬럼 도입 가능 (계획서 Alternative B 참조).

## Result

- agent01-claude / codex agents가 attached MCP 툴을 프롬프트 없이 호출 가능
- gemini-cli는 기존 `--approval-mode yolo`로 동일 동작 유지 (변경 없음, 회귀 테스트 통과)
- agent 단위 테스트 133/133 통과 (기존 131 + 신규 2)
- cluster 테스트 568/568 통과 (관련 없지만 회귀 확인)
- 커밋 디프: 4 files, +112/-15 lines
- admin의 `.claude/settings.json` 커스텀 권한 정책과는 독립적으로 동작 — merge 레이어 수정 없음
- 보안 트레이드오프: agent가 workspace 내부에서 자유롭게 툴 호출. 외부 접근은 MCP 서버 자체가 차단 (filesystem MCP의 MCP_FS_ALLOWED_PATH 등). 시스템 격리는 유지
