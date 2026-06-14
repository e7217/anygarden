# docs(readme): switch Quick Start from uvx to uv tool install (#443)

- Commit: `9f837a3` (9f837a3 on branch docs/readme-uv-tool-quickstart)
- Author: Changyong Um
- Date: 2026-06-15
- PR: #443 (issue) — PR number assigned on open

## Situation

#437/#441로 정비된 README의 Quick Start는 모든 설치/실행을 `uvx --from "anygarden[…]"`
(임시 1회 실행)으로 안내했다. 서버·머신 데몬은 반복 실행되는 장기 프로세스라 매 호출마다
환경을 재해석하는 uvx보다 uv 네이티브 영구 설치(`uv tool install`)가 더 자연스럽다는 사용자
요청에 따라 전환.

## Task

- Quick Start의 uvx 기반 설치/실행을 `uv tool install` 기반으로 교체.
- 일관성 유지: README 내 모든 uvx 사용처(Prerequisites 노트, Ollama step 4 포함)도 함께 전환.
- 정보 손실 없이 동일 호스트/원격 호스트 시나리오와 PATH 노출 주의사항을 보존·보강.

## Action

`README.md` 한 파일 (+22/−17), uvx 5곳 → uv tool install:
- Prerequisites uv 항목(`README.md:59`): `uvx` 런너 설명 → `uv tool install` 설명. pip 대안 유지.
- Quick Start intro(`:72`): "install it as a uv tool" + `uv tool install`가 `anygarden`을
  `PATH`에 올린다는 설명 + `uv tool update-shell` 안내.
- Quick Start step 1(`:79`): `uv tool install "anygarden[server]"` → `anygarden server init`
  → `anygarden server …`.
- Quick Start step 3(`:95`): `uv tool install "anygarden[machine]"` → `register` → `run`.
  동일 호스트 시 `"anygarden[server,machine]"` 동시 설치, 원격 시 reachable 주소 안내를 주석에 보존.
- Ollama step 4(`:176`): `uv tool install "anygarden[machine]" --with "openhands-sdk>=1.21"`
  후 `anygarden machine run`.

검증: `uv 0.7.13`에서 `uv tool install`의 extras(`pkg[extra]`)·`--with` 지원 확인.
README에 `uvx` 잔재 0건(grep) 확인.

## Decisions

- **`uv tool install`(영구 tool 설치) 채택** — uvx(=`uv tool run`)의 영구 대응물. 대안:
  - `uv venv` + `uv pip install` + `uv run`/activate — 단계가 많고 데모 UX가 무거움, 기각.
  - uvx 유지 — 사용자가 명시적으로 uv 기반을 요청, 반복 실행 프로세스에 부적합, 기각.
- **README 전역 일관 전환**: 사용자는 "Quick Start"를 지목했으나, Prerequisites 노트가
  "uvx 런너"를 직접 언급하고 Ollama step 4도 uvx를 써서, Quick Start만 바꾸면 문서가 모순됨.
  정확성·일관성을 위해 사용법 5곳 모두 전환.
- **동일 호스트 분기 명시**: `uv tool install`은 같은 tool 이름(`anygarden`)을 재설치로 덮으므로,
  한 호스트에서 server+machine을 모두 쓰려면 `"anygarden[server,machine]"`가 필요. 이를 step 3
  주석에 안내해 단일 호스트 트라이얼이 깨지지 않도록 함.
- **design 문서의 uvx는 유지**: `docs/design/09-comparison.md`의 uvx 언급은 과거 Plan 비교
  기록(사용법 아님)이라 범위에서 제외.
- 가정: `anygarden` 패키지의 `[project.scripts] anygarden = anygarden.cli:dispatch`가 유지되어
  tool install 시 `anygarden` 엔트리포인트가 노출됨. 엔트리포인트명이 바뀌면 재검토.

## Result

- Quick Start가 uv 네이티브 영구 설치 흐름(`uv tool install …` → `anygarden …`)으로 통일.
  PATH 주의·동일/원격 호스트 분기 안내 포함. 문서 전용 변경(코드 무영향).
- 최종 검증은 PR #443 CI 그린으로 확인.
