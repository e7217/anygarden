---
id: 4
title: Embed LiteLLM proxy as a subprocess inside doorae-server
status: accepted
date: 2026-04-20
---

# 4. Embedded LiteLLM Gateway

## Context

doorae 에이전트의 LLM 호출은 현재 **에이전트 프로세스가 직접 업스트림 API**(`api.anthropic.com`, `api.openai.com` 등) 로 나가는 구조다. `doorae-machine` 이 spawn 한 subprocess 의 `os.environ` 에 API 키만 있으면 동작한다 ([001-engine-subprocess](./001-engine-subprocess.md), [#184](https://github.com/e7217/doorae/issues/184)).

이 구조는 세 가지 제약을 만든다:

1. **인터넷이 없는 머신에서 동작 불가**. 예컨대 내부망에만 있는 머신 B 에서 Claude Code 에이전트를 돌리고 싶으나, `api.anthropic.com` 으로의 outbound 가 없으면 SDK 가 바로 실패한다. 인터넷이 되는 머신 A 를 경유하도록 수동으로 세팅하려면 머신마다 `ANTHROPIC_BASE_URL` + 별도 프록시 + 시크릿 배포가 필요하다. 운영자 부담이 크고, 머신 수가 늘면 무너진다.
2. **머신·에이전트·룸별 사용량 추적 불가**. 호출이 doorae 프로세스를 거치지 않으므로 누가 어떤 모델을 얼마나 썼는지 중앙에서 알 방법이 없다. 과금 모델 실험이나 abuse 탐지에 필요한 데이터가 비어 있다.
3. **엔진별로 프로토콜이 서로 다름**. Claude Code 는 Anthropic `/v1/messages`, Codex 는 OpenAI `/v1/chat/completions` 를 쓴다. 두 경로를 모두 통과시키려면 단순 reverse proxy 로는 부족하고 게이트웨이 수준의 라우팅이 필요하다.

2026 년 시점에 LiteLLM Proxy 가 이 세 문제를 한 번에 해결하는 성숙한 오픈소스로 자리잡았다 (1.83+). OpenAI 와 Anthropic 프로토콜을 **같은 포트의 서로 다른 path** 로 동시 서빙하고, 업스트림을 config 로 라우팅한다. 문제는 **어디에 어떻게 띄우는가**다.

선택지는 네 가지였다:

- **별도 프로세스** (docker-compose sidecar 또는 systemd 유닛)
- **doorae-server FastAPI 에 `app.mount()` 로 in-process 편입**
- **doorae-server 가 서브프로세스로 내장 관리** (본 결정)
- **LiteLLM 대신 단순 reverse proxy (Caddy/nginx)** — 한 프로토콜만 운영할 때

기존 doorae 스택은 **SQLite + 단일 프로세스 기동** 이 큰 설계 원칙 ([01-architecture](../design/01-architecture.md)). LiteLLM 의 spend tracking / virtual key 같은 Postgres 의존 기능을 포기하더라도 stateless 로 돌려서 기존 운영 단순성을 유지하는 편이 doorae 철학과 맞는다.

## Decision

doorae-server 가 LiteLLM Proxy 를 **서브프로세스로 lifespan 에 묶어 관리한다**. 호출 경로는 서버의 `/api/v1/llm/*` 역프록시로 단일화한다.

구체적으로:

1. **기동**: FastAPI `lifespan` 에 `LLMGatewaySupervisor` 를 물려, 서버 부팅 시 자동으로 LiteLLM 서브프로세스를 띄운다. `uv tool install 'litellm[proxy]'` 로 PATH 에 올려둔 `litellm` 바이너리를 `subprocess.Popen([<binary>, "--config", "...", "--port", "4001", "--host", "127.0.0.1"])` 형태로 spawn. `<binary>` 는 `DooraeSettings.llm_gateway_binary` (기본 `"litellm"` — PATH lookup) 가 결정하며, 운영자는 `DOORAE_LLM_GATEWAY_BINARY=$HOME/.local/bin/litellm` 같은 절대 경로로 override 가능.

   > **#364 회귀 가드**: #355 에서 `openhands-sdk` 가 transitive 로 *bare* `litellm` (proxy extras 없음) 을 monorepo venv 에 가져오면서 `.venv/bin/litellm` 이 PATH 우선순위로 이김 → spawn 시 `import backoff` 실패로 즉사 → supervisor health timeout. cluster venv 에 `litellm[proxy]` 를 직접 추가하는 길은 막혀 있다 (litellm 의 `[proxy]` extras 가 `fastapi==0.124.4` 를 핀하지만 cluster 는 `fastapi<0.120`). 그래서 `llm_gateway_binary` 설정으로 운영자가 별도 user-tool 설치를 가리키게 하는 것이 현실적 해법.
2. **listen**: `127.0.0.1:4001` 고정. 외부에는 노출하지 않는다. 유일한 접근 경로는 doorae-server 의 역프록시.
3. **역프록시**: `/api/v1/llm/*` path 로 들어오는 모든 요청은 기존 doorae 인증 미들웨어(user/agent/machine 토큰) 를 통과한 뒤, Authorization 헤더를 LiteLLM master key 로 치환해 127.0.0.1:4001 로 릴레이. SSE 스트리밍은 `httpx` + `StreamingResponse` 로 청크 단위 통과.
4. **설정**: 모델 등록 / API 키 관리는 doorae DB 의 신규 테이블 (`llm_gateway_models`, `llm_gateway_secrets`) 에 저장. config.yaml 은 이 DB 상태에서 렌더링되며, **시크릿 값은 yaml 에 절대 들어가지 않고** `os.environ/DOORAE_LITELLM_<KEY>` 참조만 쓴다. 실제 값은 서브프로세스 spawn 시 Fernet 복호화 후 `env=` 로 주입. Fernet 키는 기존 `DOORAE_MCP_SECRETS_KEY` 를 재사용.
5. **변경 반영**: 핫리로드 불가. 드래프트-Apply 패턴으로 admin 이 여러 변경을 쌓은 뒤 "Apply" 로 명시적 respawn. respawn 은 SIGTERM → 30 초 grace → SIGKILL → 새 env 로 spawn → health check 순.
6. **사용량 로깅**: 역프록시 레이어에서 응답 body 의 `usage` 필드를 파싱해 `llm_gateway_usage` 에 요청당 1 행 기록. 30 일 TTL 크론으로 정리.
7. **Admin UI**: `AdminLLMGatewayPage` 에 세컨더리 사이드바 (Models / Secrets / Status / Usage 4 개 섹션) + 하단 고정 Apply 푸터. 권한은 기존 `get_admin_identity` 재사용.
8. **에이전트 측**: Phase 5 에서 매니페스트 배포 시 `engine_secrets` 에 `ANTHROPIC_BASE_URL=<server>/api/v1/llm`, `OPENAI_BASE_URL=<server>/api/v1/llm/v1`, `*_AUTH_TOKEN=<doorae-agent-token>` 을 주입. `packages/agent/doorae_agent/integrations/claude_code.py` 와 `codex.py` 는 SDK 호출을 `secrets_in_env([...])` 로 감싼다 ([#184](https://github.com/e7217/doorae/issues/184) 인프라를 그대로 쓰지만 production 호출이 비어 있던 갭을 메움).

**Feature flag**: `DOORAE_LLM_GATEWAY_ENABLED` (default `false`). Phase 1 ~ 4 머지 중에도 기존 "머신 호스트가 직접 API 호출" 경로는 그대로 유지되며, Phase 5 에서 on 으로 전환.

## Alternatives considered

- **완전 별도 프로세스 (docker sidecar)**. 공수는 가장 낮으나 배포 아티팩트가 2 개로 늘고, B 머신 시나리오에서 얻는 "머신은 doorae-server URL 하나만 알면 된다" 라는 단순성이 희석된다. 운영 docs 분량도 2 배. **기각**.
- **FastAPI `app.mount()` 로 in-process 편입**. litellm 의 proxy FastAPI 앱을 import 해서 mount. 확인 결과 가능은 하지만 LiteLLM 팀이 공식 지원하지 않으며 (공식 배포 경로는 CLI/Docker), `lifespan` 이 sub-app 에서 자동 실행되지 않아 수동 chain 이 필요하고, Prisma 기반 DB 초기화·미들웨어 격리·설정 파일 로딩이 모두 mount 경계에서 편집된다. LiteLLM 버전 업마다 깨질 위험 큼. 기대값 대비 유지보수 비용 과도. **기각**.
- **Caddy/nginx reverse proxy**. LiteLLM 없이 Anthropic API 로 직접 투명 프록시. 가장 가벼우나 단일 프로토콜만 지원, 인증 바디 변환/사용량 측정 불가. Claude Code 하나만 쓸 거면 맞지만 Codex 가 섞이는 순간 한계. **유보** — 1 엔진 소규모 배포엔 여전히 유효한 옵션이라 본 결정은 그것을 막지 않음.
- **Postgres 도입해서 LiteLLM full feature 사용**. virtual key UI, 영구 spend tracking, 유저/팀 관리를 네이티브로 얻음. 그러나 doorae 전체 스택이 SQLite + 단일 프로세스로 설계됐고 (`sqlite+aiosqlite`, `~/.doorae/doorae.db`), 이걸 깨는 변화는 본 결정 하나 때문엔 과도. 대신 doorae DB 에 자체 테이블 3 개만 추가하고 LiteLLM 을 stateless 로 운용. **기각**.
- **`uvx` ephemeral 실행**. 설치 없이 매번 `uvx --from 'litellm[proxy]' litellm ...`. 캐시 GC/버전 drift/subprocess 호출 경로에 wrapper 한 겹이 추가됨. 장수 서버 사이드카엔 부적합. **기각** — `uv tool install` 로 영구 설치.
- **첫 모델 등록 시 게이트웨이 lazy start**. idle 자원을 아낄 수 있으나 "프로세스가 아직 안 떴다" 경로가 분기마다 필요해져 상태 머신이 2 배 복잡해지고 race 여지. 운영 안정성 > 50MB RSS 절약. **기각** — 항상 기동, 모델 0 개여도 유지.
- **즉시 반영 (Apply 버튼 없음)**. 편집할 때마다 respawn 되면 실수/중간 상태에서 타 요청이 5xx. 드래프트-Apply 가 정석. **기각**.

## Consequences

**긍정적:**

- 인터넷 없는 머신에서도 doorae-server URL 하나만 reachable 하면 LLM 호출 가능 — B 머신 시나리오 해결
- 기존 doorae 인증 (JWT / agent token / machine token) 이 LLM 접근 게이트까지 겸함 — 별도 virtual key 체계 불필요
- 룸/에이전트/모델별 사용량이 doorae 의 SQLite 에 기록 — admin UI 에서 바로 조회, 과금 모델 실험에 활용
- 배포 아티팩트는 여전히 doorae-server 1 개 (uv tool install 로 PATH 에 보이는 `litellm` 만 추가). `make install` 한 줄로 통합
- LiteLLM 의 프로토콜 라우팅 능력 (`/v1/messages` + `/v1/chat/completions` 동시) 을 그대로 활용 — Claude Code / Codex 가 섞여도 단일 엔드포인트
- Feature flag off 에서 기존 경로 완전 보존 — 기존 사용자 영향 0

**부정적:**

- doorae-server 에 서브프로세스 supervision 책임이 늘어남 — health check, crash 재시작, graceful shutdown, SIGTERM/SIGKILL 타이밍 관리. 단위 테스트 복잡도 상승.
- LiteLLM CLI 계약 (`--config`, `--port`, `--host`, exit codes) 변경 시 doorae 가 깨질 수 있음. 버전 pinning + CHANGELOG 모니터링 필요.
- SSE 스트리밍 릴레이에서 클라이언트 disconnect 시 upstream 연결 leak 가능성. `httpx` finally aclose() 필수.
- LiteLLM 의 virtual key / persistent spend tracking / UI 는 포기 (stateless 운용). 대체 기능은 doorae 자체 구현.
- 시크릿을 환경변수 참조로만 넘기므로 admin 이 잘못된 env_var_name 을 지정하면 런타임에만 발견됨. UI 에서 "Test" 버튼으로 조기 발견 필요.
- 역프록시 레이어가 모든 LLM 트래픽을 통과 — doorae-server 가 SPOF 이자 대역폭 병목. 트래픽 규모가 커지면 재평가 필요 (초기 범위엔 무관).

**기각된 대안 대비 이점:**

- docker sidecar 대비: 배포 아티팩트 1 개, 운영 docs 단순화, B 머신 시나리오의 "한 URL" 단순성 유지
- mount 대비: LiteLLM 의 공식 지원 경로 (CLI) 만 사용 → 버전 업 내성 높음
- Postgres 도입 대비: doorae 전체 스택의 SQLite + 단일 프로세스 원칙 보존
- Caddy 대비: 멀티 엔진 (Anthropic + OpenAI) 동시 지원, 사용량 로깅 가능

## 관련 문서

- 구현 계획: `.tmp/plan-197-embedded-litellm-gateway.md`
- 상세 설계: [`docs/design/12-llm-gateway.md`](../design/12-llm-gateway.md)
- 선례 서브프로세스 결정: [`docs/decisions/001-engine-subprocess.md`](./001-engine-subprocess.md)
- engine_secrets 인프라: [#184](https://github.com/e7217/doorae/issues/184)
