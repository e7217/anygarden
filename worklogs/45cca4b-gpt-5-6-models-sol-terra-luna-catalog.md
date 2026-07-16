# feat(engines): GPT-5.6 모델(sol/terra/luna) 카탈로그 추가

- Commit: `45cca4b` (45cca4b22e66da032ea3b7c5f3e411f527fae728)
- Author: Changyong Um
- Date: 2026-07-16T17:40:20+09:00
- PR: —

## Situation

OpenAI가 2026-07-09 GPT-5.6 패밀리(Sol/Terra/Luna 3개 티어 + max reasoning)를
출시했으나, 엔진 모델 카탈로그(`packages/cluster/anygarden/engines/catalog.py`)는
2026-04-25(codex 0.121.0) 기준으로 멈춰 있어 codex-cli 최신 모델이 `gpt-5.5`에
머물러 있었다. 관리자가 에이전트를 만들 때 GPT-5.6 티어를 고를 수 없었다.

## Task

- codex-cli / openhands 엔진 카탈로그에 GPT-5.6 3개 티어를 추가한다.
- 실제 codex CLI가 받는 모델 ID를 쓴다(마케팅 코드네임이 아니라). 카탈로그
  헤더의 원칙 — "벤더 문서가 아니라 로컬 CLI 바이너리를 신뢰" — 을 지킨다.
- 이번 세대 신규 reasoning level을 정확히 반영한다.
- 카탈로그를 소비하는 테스트·어댑터 fallback이 깨지지 않게 동기화한다.

## Action

- `packages/cluster/anygarden/engines/catalog.py`
  - 헤더 docstring을 codex 0.144.1 기준으로 갱신하고 GPT-5.6 검증 절차·근거를 기록.
  - codex-cli 엔진: `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna`를 모델 목록
    맨 앞에 추가(각 per-model reasoning `minimal~max`), 엔진 레벨 reasoning에
    `max` 추가, `default_model`을 `gpt-5.6-terra`로 지정.
  - openhands 엔진: OpenAI 섹션에 `openai/gpt-5.6-sol/terra/luna` 미러 추가.
- `packages/agent/anygarden_agent/integrations/codex_cli.py:154` — 무인자 어댑터
  fallback default를 `gpt-5.6-terra`로 동기화(카탈로그 default와 일치시키라는
  코멘트 추가).
- `packages/agent/anygarden_agent/cli.py:213` — 기본 모델 주석 갱신.
- 테스트: `test_engine_catalog.py`(default·티어·`max` 커버리지),
  `test_codex_cli.py`(무인자 어댑터 usage model) 갱신.

## Decisions

- **모델 ID 형태**: codex 0.144.1 바이너리의 model-preset 테이블을 `strings`로
  뜯어 slug가 코드네임 그대로(`gpt-5.6-sol/terra/luna`)임을 확인. 마케팅 티어명을
  임의 API ID로 추측하지 않고 바이너리를 source of truth로 삼음. 추가로 라이브
  `codex exec -m gpt-5.6-sol` 라운드트립으로 백엔드 수용을 확인(가짜
  `gpt-5.6-zzz`는 거부됨).
- **reasoning level `max` vs `ultra`**: 서버 검증 에러가 유효 effort를
  `none/minimal/low/medium/high/xhigh/max`로 명시 → `max`가 신규. OpenAI 발표에
  나온 `ultra`는 `reasoning.effort` 값이 아니라 codex CLI의 multi-agent 실행
  모드였으므로 카탈로그 reasoning_levels에서 **의도적으로 제외**. (혼동 시
  재검토 트리거: codex가 `reasoning_effort=ultra`를 API param으로 받기 시작하면
  추가.)
- **default_model 선택**: 후보는 (1) flagship `sol` — 기존 관례(새 세대 flagship을
  default로, 직전 `gpt-5.5`가 flagship급), (2) balanced `terra` — 비용/품질 균형.
  최초엔 관례대로 `sol`로 두었으나, flagship은 $5/$30로 일상 에이전트 기본값으로는
  과함. 일상 작업 비중이 높다는 판단에 따라 **`terra`($2.50/$15)**로 결정. `sol`은
  모델 목록에 남아 있어 태스크별로 선택 가능. (재검토 트리거: 기본 에이전트
  워크로드가 고난도 위주로 바뀌면 `sol`로 상향.)
- **어댑터 fallback 동기화**: fallback은 model 미지정 시 최후 방어선이라 프로덕션
  경로에선 거의 안 쓰이지만, "default를 terra로"의 일관성을 위해 함께 상향. 명시적
  `model="gpt-5.5"`를 쓰는 usage-extraction 테스트들은 5.5가 카탈로그에 그대로
  남아 유효하므로 건드리지 않음.

## Result

- codex-cli / openhands 엔진이 GPT-5.6 Sol/Terra/Luna를 노출, 기본은 `gpt-5.6-terra`.
- 검증: cluster `test_engine_catalog.py` 25 passed, agent `test_codex_cli.py`
  22 passed, ruff all passed. API 엔드포인트 테스트가 default·티어 노출을 확인해
  end-to-end 검증됨.
- 후속 여지: 카탈로그는 여전히 수작업 유지(헤더의 issue #4 dynamic refresh 미구현).
