# feat(agent): codex CLI exec 어댑터 추가 (codex-cli 엔진) (#496)

- Commit: `aee527b` (aee527bfe2bb970ce06888ae8c417e3c6249d770)
- Author: Changyong Um
- Date: 2026-06-24T10:48:43+09:00
- PR: #496

## Situation

codex 엔진 어댑터(`integrations/codex.py`)는 codex-python SDK를 in-process로 사용하고, 이 SDK는 휠에 vendored codex-cli 바이너리를 번들한다. 그 결과 **SDK 버전 ↔ 번들 바이너리 ↔ 지원 모델**이 3중으로 결합된다. 2026-06-24, 번들 바이너리(codex-cli 0.122)가 어댑터 기본 모델 `gpt-5.5`를 몰라 `400 invalid_request_error`를 반환했고, 사용자에게 "에이전트가 응답을 생성하지 못했습니다."만 반복 노출되는 장애가 발생했다(codex-python>=1.141 핀으로 1차 수습). 이 결합은 codex가 새 모델을 낼 때마다 SDK 추적과 `parse_notification` shim(#190) 유지를 강요하는 구조적 부채다.

## Task

- codex 바이너리를 통합 코드에서 분리해 버전 결합을 해소한다.
- 기존 SDK `codex` 엔진의 동작/세션/텔레메트리(토큰 usage)·권한 tier 의미론을 잃지 않는다.
- 운영 중인 codex 에이전트에 영향을 주지 않는다(위험 없는 PoC).
- gpt-5.5 미지원 장애의 직접 수습(codex-python 핀)도 같은 브랜치에 포함한다.

## Action

- **신규 어댑터** `packages/agent/anygarden_agent/integrations/codex_cli.py`: `CodexCliAdapter` + `integrate_with_codex_cli`. `gemini_cli.py`의 CLI 서브프로세스 패턴(subprocess group kill, timeout→`EngineTimeoutError`, 비정상 종료→`EngineError`, supervisor/typing/3-state gate/delegate/room_query 라우팅)을 차용.
  - `_resolve_codex_cli_args`: codex.py의 `_resolve_codex_flags`(`_CODEX_TIER_FLAGS`)를 재사용해 tier→`-s <sandbox> -c approval_policy=<p>` CLI 인자로 변환.
  - `_call_codex`/`_exec_once`: `codex exec [resume <id>] --json --skip-git-repo-check -C <cwd> <tier flags> -m <model> [-c model_reasoning_effort=..] -o <tmpfile> -`(프롬프트 stdin). 룸별 `thread_id`로 resume, 만료 시 새 세션 1회 재시도.
  - `_parse_codex_jsonl`: `thread.started`(세션 id)/`item.completed` agent_message(응답)/`turn.completed`(usage)만 처리, 미지 type은 skip → SDK용 shim 불필요.
  - 응답은 `-o` last-message 파일 우선, JSONL agent_message 폴백. usage는 `_extract_usage`로 input/output tokens 매핑(cost=None).
- **등록**: `integrations/__init__.py`(ENGINES/_ADAPTER_CLASSES), `cli.py::_setup_engine`(elif `codex-cli`).
- **노출**: `engines/catalog.py`(codex 복제한 `codex-cli` 엔트리), `machine/detector.py`(`BINARY_ENGINES`에 `("codex-cli","codex")`), `machine/spawner.py`(`engine == "codex"` 4개 분기 — workspace 마커/auth overlay/마커 생성/CODEX_HOME redirect를 `in ("codex","codex-cli")`로 확장), `AdminMachines.tsx`(엔진 라벨 `'codex-cli': 'Codex CLI (exec)'`).
- **핀**: `packages/agent/pyproject.toml` `codex-python>=1.114` → `>=1.141`.
- **테스트**: `tests/test_integrations/test_codex_cli.py`(tier 매핑·JSONL 파서·usage·resume/fallback·on_message 20 케이스).

## Decisions

- **신규 엔진 공존 vs 기존 codex 치환** → 공존. detector에서 `("codex-cli","codex")`로 같은 바이너리를 다른 엔진명으로 광고할 수 있어 공존 비용이 "카탈로그/스폰 분기 추가" 수준으로 낮고, 운영 중 SDK codex 에이전트 영향이 0이며 A/B 비교가 가능하다. 치환은 검증 전 전면 전환이라 PoC 성격과 충돌해 기각.
- **세션: codex `resume` vs gemini식 무상태 재조립** → resume. `codex exec`가 `resume <id>`를 1급 지원하고 `thread.started`로 id를 즉시 돌려주므로, 룸별 thread_id 저장 + 만료 fallback만으로 SDK codex와 동등한 네이티브 세션을 보존한다(매 턴 전체 transcript 재전송 회피). 무상태 재조립은 "기능 유지" 목표에서 세션을 후퇴시켜 폴백 경로로만 남김. 가정: codex 세션이 디스크에 영속되어 다음 spawn에서도 resume 가능 — 틀어지면 무상태 폴백으로 재검토.
- **tier→플래그 매핑 재사용** → SDK codex의 `_CODEX_TIER_FLAGS`를 그대로 CLI로 변환. 두 codex 엔진이 동일 권한 모델을 공유해야 운영 혼선이 없다. 0.140/0.141에서 sandbox 값(read-only|workspace-write|danger-full-access)·approval_policy 값(untrusted|never|…)을 실측해 매핑 확정.
- **응답 추출: `-o` 파일 + JSONL 병행** → 모르는 JSONL type을 무시해도 되는 특성이 곧 디커플 이점(shim 제거)이라, 파서는 3개 type만 처리하고 최종 텍스트는 `-o` 파일을 1순위로 둬 견고성을 확보.
- **codex-python 핀 동봉** → codex-cli는 SDK에 의존하지 않지만, SDK `codex` 엔진의 gpt-5.5 장애를 같은 PR에서 끝내 main을 일관 상태로 만든다.

## Result

- agent 509 passed(기존 489 + 신규 20, ruff 통과), cluster catalog/engine 72 passed(codex-cli 엔트리·gpt-5.5 유효), machine detector/spawn 69 passed(BINARY_ENGINES 등록), frontend `tsc --noEmit` 무오류.
- codex 엔진을 SDK 버전 결합 없이 운용할 수 있는 `codex-cli` 엔진이 추가됐고, 기존 `codex`(SDK)와 공존한다.
- 후속(범위 밖): 장수 app-server 재사용 지연 최적화, codex 세션 파일 정리 정책, API-key auth codex.
