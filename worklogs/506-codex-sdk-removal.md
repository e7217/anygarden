# refactor(agent): SDK codex 엔진(codex.py) 제거 — codex-cli(exec)로 일원화 (#506)

- Commit: `9944459`
- Author: Changyong Um
- Date: 2026-06-24
- PR: #506

## Situation

#502에서 SDK codex 엔진(`integrations/codex.py`)을 deprecated로 마킹했다. codex-python SDK는 번들 바이너리·모델 버전이 강하게 결합돼(gpt-5.5 outage의 근본 원인) #190 parse_notification shim까지 필요했다. #496에서 도입한 codex-cli(`codex exec --json` subprocess) 엔진이 이 결합을 끊은 검증된 대체재로, 이제 SDK codex를 코드에서 제거할 차례였다.

## Task

- codex.py와 codex 엔진 등록(`__init__`/`cli`/`catalog`/`detector`/`spawner`)을 제거하되 codex-cli는 온전히 유지.
- codex_cli.py가 codex.py에서 import하던 공유 심볼을 이동(삭제 선행 조건).
- codex-python 의존성 + #190 shim 제거.
- 기존 `engine='codex'` 에이전트가 깨지지 않도록 DB 마이그레이션.
- 테스트/프론트/문서 정리.

## Action

- **공유 심볼 이동**: `_CODEX_TIER_FLAGS`/`_resolve_codex_flags`/`_codex_thread_cwd`를 codex.py→`codex_cli.py`로 옮겨 자족화, `codex_cli.py:44` import 제거. codex.py 삭제.
- **등록 제거**: `integrations/__init__.py`(ENGINES/_ADAPTER_CLASSES), `cli.py`(`_setup_engine` codex 분기 + `_ENGINE_TIMEOUT_KEY` codex), `engines/catalog.py`(codex 엔트리), `machine/detector.py`(`("codex","codex")`), `machine/spawner.py`(4분기 `in ("codex","codex-cli")`→`== "codex-cli"`).
- **의존성**: `pyproject.toml`에서 `codex-python` + `codex` extra 제거(`uv.lock` 갱신).
- **MCP 템플릿 버그 수정**: `mcp_templates/merge.py`(L66 settings-path, L281 self-MCP, L310 dispatcher)와 `builtin.py`(config_per_engine 키 + 5개 supported_engines)가 `"codex"`만 처리해, 마이그레이션 후 codex-cli 에이전트가 self-MCP/템플릿을 못 받던 누락(#496 도입 시 빠짐)을 `codex-cli`로 전환해 수정.
- **마이그레이션**: `db/migrations/versions/050_migrate_codex_to_codex_cli.py` — `UPDATE agents SET engine='codex-cli' WHERE engine='codex'`(downgrade no-op).
- **timeout 유지**: `_turn_timeout._ENGINE_DEFAULTS["codex"]`는 codex-cli가 `resolve_turn_timeout("codex")`로 참조하므로 유지(주석만 갱신).
- **frontend**: `AdminMachines` ENGINE_LABELS codex 제거(codex-cli→'Codex CLI'), `mcpTemplateForm`/`AdminMCPTemplates` SUPPORTED_ENGINE_IDS·config, `ManifestPanel` ENGINE_PREFIXES를 codex→codex-cli, 주석 갱신.
- **테스트/문서**: test_codex.py 삭제, test_codex_permission_mapping import 갱신, test_engine_catalog·test_migrations·test_spawner·test_materialize·test_mcp_templates_* 갱신, docs/engines.md·README·CodexAdapter 주석 갱신.

## Decisions

- **마이그레이션 vs unavailable**: 마이그레이션 채택. unavailable은 코드가 더 적지만 기존 codex 에이전트가 어댑터 없이 spawn 불가 → 사용자 요구("깨지지 않게") 위배. codex-cli가 동일 모델/권한/워크스페이스 격리로 검증돼 전환이 안전.
- **공유 심볼 위치**: 새 공유 모듈 대신 codex_cli.py 직접 정의. 유일 소비자가 codex_cli뿐이라 간접층이 과함.
- **`_ENGINE_DEFAULTS["codex"]` 유지**: cli.py `_ENGINE_TIMEOUT_KEY["codex"]`(dead)만 제거하고 timeout 값 키는 유지. 두 dict의 역할이 다름 — codex-cli가 "codex" 프로필을 참조하므로 제거 시 #500 spawn crash 재발.
- **MCP 템플릿 누락 발견**: 초기 조사(catalog/api/models 중심)가 mcp_templates를 놓쳤고, 구현 후 Explore 에이전트의 adversarial 검증에서 드러났다. codex-cli는 codex 바이너리와 동일하게 `.codex/config.toml` MCP를 쓰므로 codex→codex-cli 전환이 정답.
- **범위 밖 유지**: agent-ts `case "codex"`(TS 런타임 MVP는 claude_code만 지원, codex/codex-cli 모두 out-of-scope 에러)와 test_daemon/test_protocol_frames의 `engine="codex"`(daemon이 opaque 라벨로만 다룸, 동작 무관)는 유지. machine cli.py/config.py의 ruff 부채는 pre-existing이라 미수정.

## Result

- 전체 테스트 통과: agent 479 / cluster 1222 / machine 375, frontend tsc + vitest(mcpTemplateForm) 25.
- 마이그레이션 050: head 체인 정상, UPDATE 로직 + up/down 라운드트립 검증(test_migrations 14).
- ruff: 변경 파일 전부 통과. SDK codex 식별자(CodexAdapter/integrate_with_codex/integrations.codex) 잔존 0.
- MCP 렌더 버그 수정으로 마이그레이션된 codex-cli 에이전트가 self-MCP/템플릿을 정상 수신.
- 운영 DB(`~/.anygarden/anygarden.db`) `alembic upgrade head` 적용은 병합 후 수행 예정.
