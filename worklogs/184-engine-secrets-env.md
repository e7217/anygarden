# refactor(machine): inject engine_secrets via subprocess env, not disk .env (#184)

- Commit: `3cc00b3`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #184

## Situation

`Spawner._materialize_agent_dir`가 `SpawnManifest.engine_secrets`(API 키 등)를 엔진별 `.env` 파일로 디스크에 렌더했다 — `.gemini/.env`, `.codex/.env`, `.claude/.env`, 모두 mode 0o600. 문제는 에이전트 subprocess cwd가 에이전트 디렉토리 안(보통 `workspace/`)이라 `../.claude/.env` 경로로 파일에 닿을 수 있고, claude-code의 경우 `workspace/.claude -> ../.claude` symlink 브릿지까지 있어 sandbox 내부에서 **직접** read 가능했다. LLM이 `Read` tool로 평문 API 키를 노출시킬 수 있는 경로가 상시 열려 있던 상태.

또한 `ManifestStore`는 `engine_secrets`를 디스크에 **영속화하지 않는다**고 `_EXCLUDED_FIELDS`(manifest_store.py:28)로 명시해 두었는데, 런타임에는 materialize가 같은 secret을 디스크에 쓰는 모순이 있었다.

## Task

- `engine_secrets`를 디스크가 아닌 subprocess 환경 변수로 주입
- 기존 `.env` 쓰기 경로 제거
- 악의적/버그성 매니페스트가 `DOORAE_TOKEN`을 `engine_secrets`로 덮어쓰는 시도를 무력화
- Gemini 어댑터 docstring의 구시대 설명 정리
- 기존 3엔진 테스트 회귀 없음

## Action

- `packages/machine/doorae_machine/spawner.py:598-614` — `env = os.environ.copy()` 직후 `env.update(msg.engine_secrets)` 추가, 그다음에 `env["DOORAE_TOKEN"] = msg.agent_token`으로 덮어씀. 순서가 방어 로직: secrets → token. 주석으로 세 레이어(데몬 env / per-agent secrets / agent token) 규칙 명시
- `packages/machine/doorae_machine/spawner.py:124-131` — `_ENGINE_ENV_PATHS` dict 제거
- `packages/machine/doorae_machine/spawner.py:395-400` — `.env` write 블록을 제거하고 "engine_secrets는 이제 subprocess env로 흐른다"는 짧은 주석으로 대체
- `packages/machine/tests/test_spawner.py:44-128` — 신규 `TestSpawnEnvSecrets` 클래스:
  - `test_engine_secrets_forwarded_to_subprocess_env` — `create_subprocess_exec(env=...)` 에 전달된 dict에 secret key/value가 들어있고 `DOORAE_TOKEN`은 보존됨
  - `test_engine_secrets_cannot_override_doorae_token` — manifest가 `engine_secrets={"DOORAE_TOKEN": "hijack"}`으로 보내도 `"tok-real"`이 이김
- `packages/machine/tests/test_materialize.py:340-358` — 기존 `test_engine_secrets_rendered_for_gemini`를 `test_engine_secrets_not_persisted_to_disk`로 대체. 세 엔진 모두 `.env` 부재 검증
- `packages/agent/doorae_agent/integrations/gemini_cli.py:30-35, 64-68` — 모듈 docstring과 `GeminiCliAdapter` docstring 업데이트 — `.gemini/.env` materialization 언급 제거하고 #184 정책 명시

## Decisions

`.tmp/plan-184-engine-secrets-env-injection.md`의 대안 비교:

- **A — env var 직접 주입** ← 선택. 표준 unix 패턴, 디스크 흔적 0
- **B — 디스크 `.env` 유지 + unlink 후 inherited fd**: 엔진 CLI가 fd 모델 지원 안 함
- **C — tmpfs에 `.env`**: 머신 환경 의존, 효과 모호
- **D — 외부 API 키만 env, 나머지 디스크 유지**: 부분 해결, 예외 복잡

결정적 근거는 ManifestStore가 이미 engine_secrets를 영속화 금지로 선언했다는 점. materialize가 이를 위반하는 것이 원래 bug에 가까운 구조였고, subprocess env는 POSIX 표준에 맞는 단순한 해결이다. 세 엔진 CLI 모두 자신의 표준 env var 이름(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`)을 지원하며, 기존 테스트 픽스처(`test_materialize.py`, `test_protocol_frames.py`)가 이미 이 명명 규칙을 썼기 때문에 서버 스키마 쪽의 추가 변경이 불필요했다.

가정:
- `engine_secrets` dict의 key는 env var 이름이어야 한다는 암묵 계약. 서버 측 admin UI/API 스키마 상세 검증은 이번 이슈 밖이지만, 테스트 픽스처와 현재 관습상 안전
- 엔진 CLI가 자기 **부모 프로세스** env에서 API 키를 읽는다는 전제. Gemini docstring이 이를 명시(`GEMINI_API_KEY` env var)하며, Claude Code SDK / Codex CLI도 관례상 동일
- 만약 특정 엔진이 디스크 `.env`만 지원한다면 해당 엔진에만 예외를 둬야 함 — 현재 Gemini/Codex/Claude 3개는 env var로 인증 가능

## Result

- `uv run pytest` (packages/machine) 246개 통과 (기존 244 + 신규 2)
- 에이전트 테스트 회귀 없음 (pre-existing `test_openai.py::test_integrate_registers_handler` 실패는 `OPENAI_API_KEY` 환경 미설정 문제, 본 변경과 무관 — stash 비교로 확인)
- `.env` 파일 disk 쓰기 경로 제거. `Read` tool을 통한 API 키 유출 경로 차단
- `DOORAE_TOKEN` 덮어쓰기 방어로 매니페스트 변조에 대한 identity 보호 추가
- 후속 #181(Gemini 격리)이 어떤 방식으로 가든, 본 변경은 직교하므로 충돌 없음
