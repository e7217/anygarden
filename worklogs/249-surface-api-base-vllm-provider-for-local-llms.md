# feat(llm-gateway): surface api_base + vllm provider for local LLMs (#249)

- Commit: `210a72f` (210a72f6cc4ab09f76a9e550024511092bd64c2c)
- Author: Changyong Um
- Date: 2026-04-23T01:52:59+09:00
- PR: #249

## Situation

LLM Gateway(#197)는 이미 관리형 LiteLLM 서브프로세스 + 매니페스트 기반 `engine_secrets` 주입을 완성해 놓은 상태였다. `ModelCreate.extra_params` Pydantic 필드·`LLMGatewayModel.extra_params JSON` 컬럼·`config_writer`의 `extra_params` 병합 로직·TypeScript `ModelCreateInput.extra_params` 타입까지 전 스택에 걸쳐 flow가 이미 잡혀 있었지만, 어드민 UI의 `ModelDialog`에는 이 확장점을 노출하는 입력이 없어 실질적으로 항상 `null`이 저장됐다. 그 결과 (a) Ollama/vLLM이 cluster 서버와 다른 호스트에 떠 있을 때 `api_base`를 지정할 수단이 없고, (b) Ollama는 auth가 없는 게 일반적인데도 `api_key_ref`가 필수로 걸려 있어 빈 시크릿을 만들어 꽂아야 했으며, (c) vLLM 프리셋이 없어 "Custom"으로 수동 조립해야 하는데 `api_base` 자체가 입력 불가라 막혔다.

## Task

- 어드민 `ModelDialog`에서 Ollama/vLLM/Custom provider 선택 시 `api_base` URL을 입력받아 `extra_params = { api_base }`로 패킹해 제출.
- 같은 제출 경로에서 `api_key_ref`를 로컬 provider에 한해 optional 화하고, placeholder/힌트 텍스트를 provider-aware 하게 바꾸기.
- 백엔드 `ModelCreate`/`ModelUpdate` 스키마와 핸들러가 blank `api_key_ref`를 로컬 provider일 때만 허용하도록 제약 완화. 클라우드 provider는 여전히 422 거부 보장.
- 렌더된 yaml의 `os.environ/DOORAE_LITELLM_OLLAMA_DUMMY` 참조가 항상 resolve되도록 supervisor child_env에 고정 placeholder 주입.
- 기존 어댑터(codex 등) 코드에는 손을 대지 않는 것이 제약. 이미 gateway의 `OPENAI_BASE_URL`/`OPENAI_API_KEY` 주입을 `secrets_in_env`로 소화하고 있어 바꿀 이유가 없음.

## Action

- **`packages/cluster/doorae/api/v1/llm_gateway.py`** — 모듈 상단에 `_LOCAL_PROVIDERS = frozenset({"ollama", "vllm", "custom"})` / `_OLLAMA_DUMMY_REF = "OLLAMA_DUMMY"` 상수 도입. `ModelCreate.api_key_ref`를 `Field(default=None, max_length=64)`로 완화하고 `ModelUpdate`도 동일. `_normalise_api_key_ref(provider, api_key_ref)` 헬퍼를 추가해 (a) blank + 로컬 provider → sentinel 저장, (b) blank + 클라우드 provider → `HTTPException(422, …)` 라는 단일 책임으로 분리. `create_model`은 `body.model_dump()`를 통과시킨 뒤 `api_key_ref`만 normalise한 결과로 덮어쓰고, `update_model`은 PATCH 본문이 `api_key_ref`를 건드릴 때만 `updates.get("provider", row.provider)`로 최종 provider를 결정해 같은 헬퍼를 거친다.
- **`packages/cluster/doorae/llm_gateway/bootstrap.py`** — `_build_spawn_params_factory`의 `child_env` 초기 dict에 `"DOORAE_LITELLM_OLLAMA_DUMMY": "sk-local"`을 MASTER_KEY와 나란히 주입. 이후 secrets 루프는 그대로 유지되므로, 어드민이 실수로 같은 이름으로 시크릿을 만들면 그 값이 override되는 last-write-wins 의미(주석에 명시).
- **`packages/cluster/frontend/src/components/admin-llm-gateway/ModelDialog.tsx`** — `PROVIDERS` 배열에 `{ id: 'vllm', label: 'vLLM (local)', upstreamPrefix: 'openai/' }` 추가. `LOCAL_PROVIDERS` Set과 `API_KEY_PLACEHOLDER` 매핑을 모듈 상수로 분리. 생성자에서 `initial.api_key_ref`가 `OLLAMA_DUMMY` sentinel이면 폼에 실제 env var 이름처럼 보이지 않도록 공란으로 초기화. `extra_params.api_base`를 풀어 `apiBase` state로 보관. `handleSubmit`은 두 검증을 갖는다: 공통(model_name/upstream) + 클라우드 provider에 한해 api_key_ref 필수. 제출 payload는 `initial?.extra_params`를 spread해 `api_base` 외 키(temperature 등)를 보존한 뒤 `api_base`를 merge/delete, 결과가 비면 `extra_params = null`. JSX는 기존 API key 블록 다음에 `isLocal` 조건부로 API base URL `<Input>` 블록을 추가. provider별 placeholder(`http://localhost:11434`, `http://localhost:8000/v1`)와 힌트 텍스트도 같이 노출. API key 블록은 `secrets.length > 0 && !isLocal` 일 때만 드롭다운을 렌더하고, 로컬 provider에선 자유 입력으로 폴백하도록 분기.
- **테스트 — `packages/cluster/tests/test_llm_gateway_admin_api.py`** — `test_create_model_ollama_allows_missing_api_key_ref` (생략 + 빈 문자열 둘 다 201 + DB에 sentinel 저장 확인), `test_create_model_cloud_provider_requires_api_key_ref` (Anthropic + 생략/빈 문자열 두 케이스 422), `test_create_model_ollama_with_extra_params_api_base` (POST + PATCH 라운드트립) 세 건 추가.
- **테스트 — `packages/cluster/tests/test_llm_gateway_config_writer.py`** — `test_ollama_model_with_api_base_extra_param` 스냅샷: `extra_params={api_base: ...}`가 `litellm_params.api_base`로 병합되고 `api_key`가 `os.environ/DOORAE_LITELLM_OLLAMA_DUMMY`로 렌더되는지 확인.
- **테스트 — `packages/cluster/tests/test_llm_gateway_bootstrap.py`** (신규 파일) — (a) 빈 DB에서도 child_env에 `OLLAMA_DUMMY` placeholder가 있음, (b) Anthropic 시크릿을 추가해도 placeholder가 유지됨, (c) Ollama + api_base 모델이 들어간 DB에서 yaml 디스크 라이트가 올바르게 `ollama/...` + `api_base` + `os.environ/...OLLAMA_DUMMY`를 포함함.

## Decisions

- **"에이전트 타입 추가" 요청을 gateway UI 필드 확장으로 재정의.** 최초 요청은 "Ollama/vLLM용 에이전트 타입을 추가하자"였다. 후보 접근을 네 가지 저울질:
  - (A) 새 `LocalLLMAdapter` (codex 상속, 50~80줄) + `ENGINES["local-llm"]`
  - (B) 기존 `OpenHandsAdapter`를 로컬 LLM 프로파일로 확장
  - (C) 순수 OpenAI-compat 어댑터로 툴 루프 직접 구현
  - (D) LLM Gateway 어드민 UI에 `api_base` + vllm provider + key optional화만 추가
  결정적 근거는 브라우저로 실제 UI를 열어 확인한 관찰이었다(`.playwright-mcp/add-model-ollama*.png`): 백엔드·DB·config_writer·TS 타입까지 `extra_params` flow가 이미 완성돼 있었고 `ModelDialog`의 폼에만 입력이 비어 있었다. codex 어댑터(`codex.py:38-41,234`)는 이미 `agent_secrets.secrets_in_env(_OPENAI_SDK_ENV_KEYS)`로 gateway의 `OPENAI_BASE_URL`/`OPENAI_API_KEY` 주입을 소화 중이라 A/B/C는 0 기능 / +α 유지비가 되는 상황. 따라서 D로 좁혔다.
- **`api_base`를 전용 Pydantic 필드가 아닌 `extra_params` 의 key로 채택.** 후보는 ① 새 컬럼/필드 추가, ② 기존 `extra_params` 재사용. ②를 택한 이유: DB 스키마·마이그레이션 변경 제로, 다른 LiteLLM 옵션(temperature, max_tokens, custom headers 등)도 같은 채널로 확장 가능, `config_writer`가 이미 `extra_params`를 `litellm_params`로 병합하는 로직을 갖춤. 트레이드오프는 필드 discoverability — 사용자가 "Which keys can I set?"을 문서 없이 알 수 없다는 점. 이는 UI 쪽의 `api_base` 1급 입력으로 주된 유스케이스는 해소되고, 그 외 키는 후속 요구가 쌓이면 같은 패턴으로 승격 가능하다고 판단.
- **빈 `api_key_ref`를 "sentinel(`OLLAMA_DUMMY`) 저장 + env var placeholder 주입" 형태로 해결.** 대안은 ① config_writer가 로컬 provider일 때 `api_key` 라인 자체를 yaml에서 생략, ② DB에 literal 값(`sk-local`)을 저장. ①은 `config_writer` 의 pure-function 성질을 깨뜨리고 provider별 분기를 더해야 해서 순현재 가치가 낮음. ②는 개별 관리자가 의미 없는 문자열을 보는 UX가 나쁨 + 실제 값이 DB에 노출. sentinel 방식은 `config_writer`가 기존 `os.environ/...` 렌더 로직을 그대로 유지하고, supervisor가 env 하나만 추가 주입하면 닫혀서 가장 조용함. 가정: LiteLLM의 Ollama 호출이 `api_key` 필드를 실제로 검증하지 않는다 — 깨질 경우 대안 ①로 전환.
- **`bootstrap.py`에 env 주입 위치를 선택.** `supervisor.py`는 state machine 만 담당하고 env는 `spawn_params_factory`(bootstrap)가 빌드하므로 그쪽이 아키텍처적으로 올바른 자리. 플랜 Step 3는 "`test_llm_gateway_supervisor.py`에 케이스 추가"라고 썼지만, 실제 코드 흐름을 따르는 게 맞아 신규 `test_llm_gateway_bootstrap.py`로 갈라냈다 (기존에 bootstrap 전용 테스트가 없어 자연스러움).
- **OLLAMA_DUMMY를 child_env 초기 dict에, secrets 루프 "전에" 주입.** 어드민이 `OLLAMA_DUMMY`라는 이름으로 시크릿을 등록하면 그 값이 기본 sentinel을 override한다. 반대 순서로 두면 관리자 의도를 무시하게 되는데, sentinel은 placeholder일 뿐이므로 override 허용이 옳음 (주석에 명시).
- **프론트 폼 state를 `extra_params` 전체가 아닌 `api_base`만 풀어서 관리.** `initial.extra_params` 에 이미 있던 다른 키(향후 temperature 등)는 제출 시 spread로 보존. 지금 UI가 노출하는 건 `api_base` 하나이므로 state를 단순하게 유지하고, 나중에 더 많은 키를 노출할 때 각자 전용 state로 뽑아낸다.

## Result

- 어드민이 `/admin/llm-gateway` → Add model 다이얼로그에서 Ollama/vLLM/Custom 선택 시 "API base URL" 입력이 나타나고, 비우면 LiteLLM 기본값, 지정하면 원격 호스트(예: `http://10.0.0.5:11434`)에도 연결 가능해졌다.
- 같은 provider에서 "API key" 는 `(optional)` 로 표시되고 공란 허용. 제출 시 백엔드가 `OLLAMA_DUMMY` sentinel로 저장하고, supervisor child_env의 `DOORAE_LITELLM_OLLAMA_DUMMY=sk-local`와 짝을 맞춰 yaml 의 env 참조가 resolve 된다.
- 어댑터 코드(`codex.py` 등)는 무변경 — 기존 `engine_secrets` 주입 경로를 그대로 재사용. 새 에이전트 타입도 필요 없음.
- 테스트: `packages/cluster/tests/test_llm_gateway_*.py` 57/57 + 전체 cluster 728/728 통과. ruff clean. 프론트엔드 `tsc -b && vite build` 정상 종료.
- 아직 안 한 것: 실제 원격 Ollama 서버를 띄워 end-to-end 확인 (dev 환경에서 관리자가 UI로 모델 추가 → Apply → `/models/{id}/test` 버튼으로 스모크). LiteLLM이 Ollama 원격 호출 시 streaming에 대한 특이 거동을 낼 가능성은 리스크로 남음 — 발생 시 `api_base` 가 문제가 아닌 LiteLLM 버전 이슈로 보고 처리 예정.
