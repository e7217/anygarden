# feat(engines): SDK codex 엔진을 deprecated로 마킹 (codex-cli exec 권장) (#502)

- Commit: `81406fd`
- Author: Changyong Um
- Date: 2026-06-24
- PR: #502

## Situation

SDK 기반 `codex` 엔진은 codex-python을 in-process로 임베드해, SDK 버전 ↔ 번들 바이너리 ↔ 지원 모델이 3중으로 결합된다(2026-06-24 gpt-5.5 미지원 장애의 구조적 원인). 또한 알 수 없는 notification에 깨지는 `parse_notification` shim(#190)을 계속 유지해야 한다. 대안으로 `codex exec --json`을 호출하는 `codex-cli` 엔진(#496/#498/#500)을 추가했고, 버전 디커플·shim 제거를 달성한 뒤 실전 E2E(생성·spawn·멀티턴·resume·도구호출[trusted])와 워크스페이스/CODEX_HOME 격리 동일성까지 검증을 마쳤다.

## Task

- 검증된 codex-cli를 신규 에이전트의 권장 경로로 만들고, SDK codex를 legacy로 신호한다.
- 단, 기존 codex 에이전트의 동작은 깨지 않는다(제거가 아니라 deprecation 마킹).

## Action

프로젝트의 기존 deprecation 패턴(`claude-code`의 `deprecated=True` + `deprecation_note`, #355/#382)을 그대로 적용했다.

- `packages/cluster/anygarden/engines/catalog.py`: `codex` 엔트리에 `deprecated=True`와 `deprecation_note`("SDK 버전 결합 … codex-cli (exec) 권장 … 기존 에이전트는 계속 동작") 추가.
- `packages/cluster/tests/test_engine_catalog.py`: "claude-code만 deprecated" 가정을 `DEPRECATED_ENGINES = {"claude-code", "codex"}`로 갱신하고, codex의 deprecation_note(`codex-cli` 포함), API 응답(`/engines/available`, `/engines/codex/models`)의 deprecation 메타데이터, codex-cli는 non-deprecated(권장 대체)임을 가드하는 테스트 추가.

`deprecated` 플래그는 엔진을 비활성화하지 않는다(catalog.py 명시) — 어드민 UI의 정렬 후순위 + legacy 배지에만 영향하고, 이미 codex에 핀된 에이전트는 계속 spawn/실행된다. 프론트엔드는 deprecation 메타데이터를 catalog API에서 동적으로 받아 렌더하므로 코드 변경이 없다.

## Decisions

- **deprecate vs 즉시 제거** → deprecate. codex-cli가 실전 검증을 마쳤지만, 운영 중 codex 에이전트가 있고 제거는 마이그레이션(DB engine 전환)·어댑터/의존성 정리를 동반한다. 프로젝트가 claude-code에서 쓴 "검증→deprecate→(별도 이슈) 제거" 단계 패턴이 동일 상황에 맞다.
- **claude-code 패턴 재사용 vs 새 메커니즘** → 재사용. `EngineCatalogEntry.deprecated`/`deprecation_note`와 UI 렌더가 이미 존재하므로, 플래그 한 쌍만 추가하면 일관된 UX(legacy 배지·정렬)를 얻는다.
- **codex-cli 단점(턴당 spawn 지연, overlay 없을 때 세션이 호스트 ~/.codex 공유)에도 권장하는 근거** → 버전 안정성·shim 제거·재시작 후 세션 복원이라는 이점이 신규 에이전트에 더 중요하다고 판단. 세션 격리 개선(overlay 없어도 CODEX_HOME redirect)은 비범위로 분리해 후속 처리.
- **비범위(SDK 제거)** → 어댑터/`codex-python` 의존성/`parse_notification` shim 제거는 deprecation 안정화 기간 후 별도 이슈. 지금 제거하면 검증 기간 없이 전면 전환이 된다.

## Result

`is_deprecated("codex")` = True, `is_deprecated("codex-cli")` = False. catalog API가 codex에 deprecation 메타데이터를 노출해 어드민 UI가 legacy 배지로 표시하고 codex-cli를 권장한다. catalog 테스트 25 passed, cluster 관련 314 passed, ruff 통과. 기존 codex 에이전트(테스트에이전트01/02)는 영향 없이 계속 동작. SDK codex 제거는 후속 이슈로 남는다.
