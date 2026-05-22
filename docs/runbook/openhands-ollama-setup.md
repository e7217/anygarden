# Runbook: OpenHands agent + Ollama via anygarden LLM gateway (#359)

> 대상: 외부 provider API key 없이 로컬 Ollama 만으로 OpenHands agent 를 동작시키려는 운영자.

## 사전 조건

- anygarden cluster + machine daemon 정상 동작 중
- Ollama 인스턴스가 reachable. 기본은 `http://192.168.100.97:11434` (또는 운영자 환경의 ollama URL)
- `openhands-sdk` 가 머신의 anygarden-agent venv 에 설치되어 있음 (#357 detector 가 advertise 가능)
- anygarden 가 #359 머지 이후 빌드

## 활성화 절차

### 1. 환경변수 설정

anygarden-server 가 두 변수를 읽어야 합니다. shell, systemd unit, docker env 등 어디에 두든 무관:

```bash
export ANYGARDEN_LLM_GATEWAY_ENABLED=true
export ANYGARDEN_CLUSTER_EXTERNAL_URL=http://localhost:8001
```

`ANYGARDEN_CLUSTER_EXTERNAL_URL` 은 agent 프로세스가 reverse proxy 를 부를 주소입니다. 단일 머신 dev 환경이라면 `http://localhost:8001`. 머신 daemon 이 별도 머신에서 돈다면 그 머신에서 reachable 한 anygarden-server URL 로 바꾸세요.

### 2. (확인 only) Ollama 모델이 gateway DB 에 등록돼있는지 확인

```bash
uv run python3 - <<'PY'
import asyncio, sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
async def main():
    engine = create_async_engine("sqlite+aiosqlite:////home/$USER/.anygarden/anygarden.db")
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT model_name, provider, upstream_model, enabled "
            "FROM llm_gateway_models WHERE enabled=1"
        ))).all()
        for r in rows:
            print(dict(r._mapping))
asyncio.run(main())
PY
```

기대 출력:
```
{'model_name': 'qwen3.6:27b', 'provider': 'ollama', 'upstream_model': 'ollama/qwen3.6:27b', 'enabled': 1}
```

비어있다면 admin UI 의 "LLM Gateway" 페이지에서 모델 등록이 필요. 또는 다음 중 하나로 직접 seed:

- admin UI 의 "Add Model" → provider=ollama, model_name=`qwen3.6:27b`, upstream_model=`ollama/qwen3.6:27b`
- 직접 SQL insert (운영자 책임)

### 3. anygarden-server 재시작

gateway supervisor 는 lifespan 에서 떠야 하므로 환경변수 적용 후 재시작 필수:

```bash
make dev   # 또는 운영 환경의 재시작 명령
```

부팅 로그에서 다음 메시지 확인:

```
llm_gateway.spawning ...
llm_gateway.health_ok port=4001
```

### 4. 헬스체크

anygarden-server 의 reverse proxy 가 살아있고 litellm 까지 reach 하는지:

```bash
curl -i http://localhost:8001/api/v1/llm/v1/models \
  -H "Authorization: Bearer <user-jwt-or-agent-token>"
```

`200 OK` + JSON body 에 `qwen3.6:27b` 가 있으면 OK. `503 LLM gateway is not enabled` 면 1번 환경변수 또는 3번 재시작 누락.

### 5. OpenHands agent 생성

admin UI 에서:

1. "Create Agent" → engine: **openhands**
2. model 드롭다운에 `qwen3.6:27b (via gateway)` 가 보여야 함 (#359 의 Phase 2 머지 효과)
3. 선택 후 agent 생성
4. room 에 추가

> **CLI 로 만들 때 주의**: `--model` 인자는 \`openai/qwen3.6:27b\` (provider prefix 포함). 이 prefix 는 OpenHands SDK 가 litellm 을 통해 anygarden 의 OpenAI-compat reverse proxy 로 라우팅하는 신호입니다 — 실제 backend 는 ollama 지만 agent 입장에서는 OpenAI 와 똑같은 모양으로 보입니다.

### 6. 메시지 전송 + 검증

room 에 메시지 보내고 응답 확인. 응답이 도착하면:

```bash
uv run python3 - <<'PY'
import asyncio, sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
async def main():
    engine = create_async_engine("sqlite+aiosqlite:////home/$USER/.anygarden/anygarden.db")
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            "SELECT model_name, prompt_tokens, completion_tokens, latency_ms "
            "FROM llm_gateway_usage ORDER BY created_at DESC LIMIT 5"
        ))).all()
        for r in rows:
            print(dict(r._mapping))
asyncio.run(main())
PY
```

`llm_gateway_usage` 에 row 가 1개 이상 기록돼있어야 정상.

## 트러블슈팅

### 무응답인데 로그에 `openhands.run_failed` + `litellm.AuthenticationError`

- 원인: agent 프로세스의 `engine_secrets` 가 비어있음
- 점검:
  1. `ANYGARDEN_LLM_GATEWAY_ENABLED=true` 가 anygarden-server 환경에 진짜 들어갔는지 (`echo $ANYGARDEN_LLM_GATEWAY_ENABLED` 또는 systemd `EnvironmentFile`)
  2. `ANYGARDEN_CLUSTER_EXTERNAL_URL` 이 빈 문자열이 아닌지 (`""` 도 가드에 걸려서 빈 dict 반환됨)
  3. agent 가 `openhands` 엔진인지 (다른 엔진은 의도적으로 secrets 안 받음 — A 옵션)

### 무응답인데 로그에 `litellm.NotFoundError: model not found`

- 원인: gateway DB 에는 모델이 있는데 litellm.yaml 이 stale (드래프트만 있고 Apply 안 됨)
- 점검: admin UI 의 LLM Gateway 페이지 → Models 탭에서 "Apply" 버튼 누름 → supervisor 재시작 → litellm.yaml 재렌더

### 503 `LLM gateway is not enabled`

- 원인: lifespan 이 supervisor 를 못 띄움
- 점검: anygarden-server 부팅 로그에서 `llm_gateway.spawn_failed` 검색. 흔한 케이스:
  - `litellm` 바이너리 미설치 → `uv tool install 'litellm[proxy]'`
  - port 4001 이미 사용 중 → 다른 프로세스 kill 또는 `litellm_gateway_port` 설정 변경

### claude-code / codex / gemini-cli agent 도 망가짐

- 원인 (가능성): 운영자가 `engine_secrets` 분기를 임의로 풀어 모든 엔진에 secrets 가 흘러감
- 정상 동작: #359 의 A 옵션은 **다른 3 엔진은 절대 영향받지 않도록** 설계됨. 만약 영향받는다면 코드 회귀 의심 — `build_engine_secrets` 가 `engine != "openhands"` 분기를 유지하는지 확인

## 향후

이 runbook 은 ollama-only 시나리오에 한정. Anthropic / OpenAI / Google API key 발급 후 gateway 로 라우팅하려면:

- gateway DB 에 해당 provider 모델 등록 + API key 등록
- `build_engine_secrets` 의 engine 가드를 풀어 claude-code / codex / gemini-cli 도 secrets 받도록 (별도 follow-up 이슈 — Phase 5 검증 + 정책 결정 후)

지금은 ollama 가 reachable 한 환경에서 OpenHands agent 가 응답하는 가장 짧은 길.
