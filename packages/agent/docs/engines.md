# Engine Adapters

## 지원 엔진

| 엔진 | 타입 | CLI/API | 설명 |
|------|------|---------|------|
| claude-code | SDK (subprocess) | Claude Agent SDK | Anthropic Claude Code (별도 CLI 프로세스를 띄움) |
| codex | SDK (subprocess) | codex-python | OpenAI Codex (별도 CLI 프로세스를 띄움) |
| gemini-cli | CLI subprocess | `gemini` | Google Gemini CLI |
| openhands | In-process Python SDK | openhands-sdk (litellm) | OpenHands V1 — Anthropic/OpenAI/Google 모두 단일 어댑터로 (#355) |

> 과거 `openai` / `anthropic` / `deep-agents` / `openhands`(CLI 형태)
> 어댑터는 #292/#294에서 제거되었습니다. `RoomHandlerSupervisor`
> 미연결 + 컨텍스트 plumbing 누락으로 사일런트 디그레이드를
> 일으켰기 때문입니다. 현재 `openhands` 엔진은 #355에서 동일 이름으로
> *in-process Python SDK* 형태로 부활했으며, 첫 PR부터 supervisor +
> 컨텍스트 plumbing을 갖췄습니다.

## CLI 기반 엔진 (claude-code / codex / gemini-cli)

호스트에 설치된 CLI를 subprocess로 띄워 stdin/stdout으로 통신한다 (ADR-001).

장점:
- 호스트의 인증 정보 그대로 사용
- 엔진 벤더가 자체 harness를 계속 튜닝
- 엔진 업데이트가 독립적

단점 (#355에서 다루는 통증):
- Task 전환 인식, idle 감지, abort 응답이 stdout 휴리스틱이라 엔진별 발산
- MCP 노출 분기 코드 누적 (#352 → #354 revert 사례)
- 컨텍스트 주입을 3곳에 동기화해야 함 (#237 / #246 / #279 / #283 / #284 / #288 / #293)

## In-process SDK 기반 엔진 (openhands)

OpenHands V1 SDK를 `doorae_agent` 프로세스 내부에서 import해서 직접 호출한다.

차별점:
- subprocess 레이어가 없음 → `Conversation.token_callbacks` 가 turn/tool/idle 경계를 구조화된 이벤트로 노출
- litellm 기반 multi-provider — 모델 ID에 `anthropic/...`, `openai/...`, `gemini/...` 같은 provider prefix를 붙여서 한 어댑터로 3 provider 커버
- typed tool system + MCP가 1급 시민 → 엔진별 분기 불필요 (Phase 1에서 통합 예정)
- `DelegateTool` 표준 sub-agent 지원 (Phase 3에서 doorae 채널과 결합 예정)

동작 방식:
1. `start()` 에서 `openhands.sdk.{LLM,Agent,Conversation}` 을 lazy import
2. 메시지 도착 시 `assemble_user_content` (#286) + `compose_session_context_suffix` (#293) 으로 prompt 구성
3. Per-room `Conversation` 인스턴스를 가져오거나 생성, 등록된 콜백이 assistant `MessageEvent` 를 캡처
4. `secrets_in_env` 컨텍스트 매니저로 자격증명을 SDK 호출 동안만 `os.environ` 에 노출 (`/proc/self/environ` 노출 방지, #184)
5. `send_message` + `run` 후 캡처된 텍스트 반환

자격증명 — `_OPENHANDS_SDK_ENV_KEYS` 가 다음 키를 브리징한다:

- Anthropic: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`
- OpenAI: `OPENAI_API_KEY`, `OPENAI_BASE_URL`
- Google: `GOOGLE_API_KEY`, `GEMINI_API_KEY`
- 기타: `LITELLM_API_KEY`, `LITELLM_BASE_URL` (proxy 모드 escape hatch)

Phase 0 모델 카탈로그(provider 별 1개씩, smoke-test 표면):

- `anthropic/claude-opus-4-7`
- `openai/gpt-5.4`
- `gemini/gemini-3-pro-preview`

Phase 4 에서 provider 매트릭스 전체로 확장 예정.

## 엔진 추가 방법

1. `integrations/` 디렉토리에 새 파일 생성
2. `EngineAdapter` ABC 구현 (`on_message`, `start`, `stop` + 필요 시 `ingest_context`)
3. `integrate_with_<engine>` 팩토리에서 `RoomHandlerSupervisor` 로 감쌀 것 (#292 회귀 방지)
4. `assemble_user_content` + `compose_session_context_suffix` 로 컨텍스트 plumbing 빠뜨리지 말 것 (#286, #293)
5. `integrations/__init__.py` 의 `ENGINES` / `_ADAPTER_CLASSES` 에 등록
6. `cli.py` `_setup_engine` 에 분기 추가
7. `packages/cluster/doorae/engines/catalog.py` 에 `EngineCatalogEntry` 추가
