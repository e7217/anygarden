# fix(agent,machine): keep engine_secrets out of agent /proc/self/environ (#184 follow-up)

- Commit: `4cb8b96`
- Author: Changyong Um
- Date: 2026-04-20
- PR: #184 (follow-up to PR #189)

## Situation

PR #189(#184)가 `engine_secrets`를 디스크 `.env` 파일에서 `doorae-agent` subprocess **환경 변수**로 이전했다. Stop-time 리뷰가 남은 노출 경로를 짚어냈다 — 에이전트 내부에서 돌아가는 LLM tool call(Bash, Read 등)이 자신의 `/proc/self/environ`을 읽거나 `env` 명령을 실행하면 방금 machine이 주입한 모든 API 키를 그대로 exfiltrate 가능. "process env" 레이어는 디스크 `.env`와 다른 모양의 같은 "sandbox에서 읽을 수 있는" 문제였다.

리뷰어의 정확한 지적: "#184 still leaves engine secrets exfiltrable from the agent process".

## Task

- `doorae-agent` 프로세스의 `/proc/self/environ`에서 engine_secrets 완전 제거
- 동시에 adapter가 secret에 접근할 수 있는 새 경로 제공 (subprocess env 주입용, in-process SDK용)
- 기존 `engine_secrets={}` production 상태와 호환 (현재 cluster lifecycle.py:478이 빈 dict 전송 중)
- 머신 테스트 회귀 방지. agent 테스트 신규 추가

## Action

- 신규 `packages/agent/doorae_agent/secrets.py` — private 메모리 저장소:
  - `_secrets: dict[str, str]` 모듈 전역 private
  - `load_from_stdin()` — 기동 시 `sys.stdin.read()`로 JSON 한 번 읽어 저장. `isatty` 가드로 interactive dev run에서 block 방지. malformed/empty/non-dict payload는 조용히 무시
  - `set_secrets` / `clear` / `get` / `all_secrets` — 조회/관리
  - `env_with_secrets(base_env, keys=...)` — subprocess spawn용 env dict 반환 (부모 os.environ 불변)
  - `secrets_in_env(keys)` context manager — in-process SDK 구성 직전 `os.environ` 임시 주입, 종료 시 prior 상태 복원 (exception 발생 시에도)
- `packages/agent/doorae_agent/cli.py:agent_main` — engine setup 전에 `agent_secrets.load_from_stdin()` 호출
- `packages/machine/doorae_machine/spawner.py:584-600` — `env.update(msg.engine_secrets)` 제거. `DOORAE_TOKEN`만 env에 유지 (에이전트 정체성 토큰, 블라스트 레이디어스 작음)
- `packages/machine/doorae_machine/spawner.py:707-745` — `stdin=asyncio.subprocess.PIPE` 요청 후 `json.dumps(msg.engine_secrets or {}).encode()` 페이로드 write → drain → close → wait_closed. `BrokenPipeError` / `ConnectionResetError`는 structlog warning 후 watch task가 exit code로 진단할 수 있게 통과
- `packages/agent/tests/test_secrets.py` (신규, 16 케이스):
  - stdin JSON 파싱 / interactive tty short-circuit / malformed / non-dict / empty
  - `os.environ`에 유입되지 않음을 명시 테스트
  - `env_with_secrets`: keys filter / base env mutation 보호 / os.environ 기본값 / 부모 불변
  - `secrets_in_env`: prior absent 복원 / prior value 복원 / unknown key 무시 / exception-safe 복원
- `packages/machine/tests/test_spawner.py:TestSpawnEnvSecrets` — 3 케이스로 재정비:
  - `test_engine_secrets_absent_from_subprocess_env` — 회귀 방지: child env에 secret 없고 stdin PIPE 요청됨
  - `test_engine_secrets_piped_to_stdin_as_json` — 정확히 한 번 write + close + JSON 페이로드만
  - `test_empty_engine_secrets_still_pipes_empty_object` — agent의 blocking read를 피하기 위해 `{}`라도 보내야 함
- 기존 mock proc 14곳에 `stdin = AsyncMock()` 설정 추가 (stdin drain/close await 경로가 새로 열렸기 때문에 MagicMock으로는 TypeError 발생). 공통 helper `_mock_proc(pid=...)`도 도입

## Decisions

문제 재평가에서 나온 대안:

- **A — stdin pipe로 비-env 채널 전송** ← 선택. 단일 JSON 페이로드로 간단, 머신이 완전 제어, EOF로 명확히 종료
- **B — WebSocket으로 런타임 push**: 복잡도 높고 bootstrap 타이밍 관리 필요 (running 선언 전 secrets 확정돼야 함)
- **C — 파일 디스크립터 passthrough**: 이식성 낮고 Python에서 구현 번거로움
- **D — host-level 인증 pre-provisioning (gcloud ADC 등)**: 다른 제품 모델, 현재 machine→agent 격리 모델과 충돌

결정적 근거: stdin은 POSIX 표준이고 `asyncio.subprocess.PIPE`로 자연스럽게 처리됨. doorae-agent가 stdin을 다른 용도로 쓰지 않으므로 충돌 없음. 빈 dict도 `{}` 로 보내는 규칙으로 agent의 `read()`가 항상 EOF를 받아 blocking 제거.

In-process SDK vs subprocess CLI의 두 갈래:
- **subprocess CLI (Gemini)**: `env_with_secrets`로 child env 명시 주입. agent의 os.environ은 여전히 clean
- **in-process Python SDK (Claude Code, Codex)**: `secrets_in_env` context manager로 SDK 구성 구간만 os.environ 임시 사용. 한계: SDK가 구성 중 MCP child subprocess를 spawn하면 해당 child는 임시 os.environ을 상속 → leak 가능. SDK가 `api_key=` param을 받아야 완전 차단. 본 patch의 범위 밖, follow-up 필요

가정 / 미해결:
- adapter 측 wiring(Gemini는 `env_with_secrets` 사용, Claude Code/Codex는 `secrets_in_env` 사용)은 **본 patch에 포함하지 않음**. 이유: 현재 `engine_secrets`가 프로덕션에서 항상 `{}` (cluster lifecycle.py:478)라 adapter 변경 필요가 없음. 서버가 실제 secret을 populate하기 시작할 때 adapter 변경을 시작할 수 있는 follow-up 이슈로 분리
- Claude Code / Codex SDK의 MCP child env leak는 라이브러리 업스트림에 `api_key=` 지원을 요구해야 완전 해결

## Result

- `uv run pytest` (packages/machine) 258개 통과 (기존 257 + stdin 검증 신규 +1, `TestSpawnEnvSecrets` 재정비 포함)
- `uv run pytest` (packages/agent) 228개 통과 (기존 212 + secrets 신규 16). pre-existing `test_openai.py::test_integrate_registers_handler` 실패는 `OPENAI_API_KEY` 환경 미설정 문제로 무관 (stash 비교로 확인)
- `doorae-agent` 프로세스의 `/proc/self/environ`에서 engine_secrets 완전 부재 → LLM 툴이 env를 dump해도 API 키 노출 경로 차단
- 후속 과제 2건 식별: (1) adapter 측 secret wiring (engine_secrets 활성화 시), (2) SDK MCP child env leak (업스트림 `api_key=` 지원 필요)
