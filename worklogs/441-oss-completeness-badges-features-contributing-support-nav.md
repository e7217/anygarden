# docs(readme): OSS completeness — badges, Features, Contributing, Support, nav (#441)

- Commit: `c88526a` (c88526a on branch docs/readme-oss-completeness)
- Author: Changyong Um
- Date: 2026-06-14
- PR: #441 (issue) — PR number assigned on open

## Situation

#437/#438에서 첫 실행 정확성 버그와 온보딩 갭을 해소한 뒤, README의 OSS 완성도 측면(배지·
Features 요약·기여 안내·내비게이션)이 여전히 비어 있었다. 4-렌즈 리뷰의 completeness/clarity
findings 중 정확성·온보딩(B 범위) 밖으로 미뤄둔 항목들이다.

## Task

- OSS 사용자/기여자가 기대하는 표준 요소 보강: 상태 배지, Features 섹션, Contributing 안내,
  Support 링크, 긴 README용 내비게이션.
- 중복/구조 정리(축약 lite): Ollama 섹션의 중복 env-vars 노트 제거, make setup 경고 가시화.
- 제약: 정보 손실 없이, 기존 톤 유지, in-page 앵커 무결성 보장.

## Action

`README.md` (+83/−10) 및 신규 `CONTRIBUTING.md`:
- 제목 아래 shields.io 배지 3종(PyPI `anygarden` / CI `ci.yml` / Apache-2.0).
- intro 뒤 **Jump to** 링크 + `## Features`(6 bullet: 멀티 엔진·분산 머신·협업 룸·로컬 모델
  게이트웨이·Web UI+API·managed lifecycles).
- `Develop` 섹션의 make setup staleness 산문을 blockquote `> **Use make setup, not a bare
  uv sync.**` callout으로 격상.
- Ollama 섹션의 중복 core-env-vars 단락 제거 → 한 줄 포인터(Develop/.env.example)로 축약.
- `## Contributing`(→ CONTRIBUTING.md) + `## Support`(gotchas/runbook → GitHub issues) 섹션
  추가(License 앞).
- `CONTRIBUTING.md` 신설: dev 셋업(make setup/dev), 패키지 레이아웃, 워크플로(squash PR·커밋
  컨벤션·worklogs), `make test`/`make lint`/frontend `npm run build`, DESIGN.md UI 규칙, 라이선스.

검증: 스크립트로 in-page 앵커 11개(README 8 + CONTRIBUTING→README 3)가 모두 실제 헤딩 슬러그와
매칭됨을 확인. 저장소 PUBLIC·PyPI `anygarden` 200으로 배지 렌더 가능 확인.

## Decisions

- **스크린샷 의도적 제외**: 워킹트리의 ~47개 PNG가 모두 05-10 전후, 즉 리브랜딩(doorae→Anygarden)
  과 디자인 시스템 마이그레이션(#435/#436, 06-11) **이전** 자산. 실제 확인 결과 헤더 로고가 옛
  "Doorae"·`admin@doorae.dev` → 현재 UI 오도. 스테일 이미지 커밋은 무이미지보다 나쁘다고 판단,
  현재 UI 기준 새 hero 캡처(앱 실행 필요)는 후속으로 분리.
- **Ollama 섹션 전체 runbook 이관 보류**: 기존 `docs/runbook/openhands-ollama-setup.md`가 #359
  전용·한국어·일부 stale(포트 8001/하드코딩 IP)이라 깨끗한 이관 대상이 아님. 위험한 대규모 이관
  대신 "중복 제거 + Jump-to 내비게이션"으로 proportion 문제를 완화. 향후 runbook 현행화 시 재검토.
- **CONTRIBUTING.md를 README 섹션이 아닌 별도 파일로**: GitHub이 "Contributing guidelines"로
  표면화하는 OSS 표준. README엔 짧은 포인터만.
- **TOC는 풀 중첩 대신 한 줄 Jump-to**: GitHub auto-outline과 중복을 피하고 유지보수 부담 최소화.

## Result

- README가 OSS 표준 요소(배지·Features·Contributing·Support·내비게이션)를 갖추고, 중복 1건 제거·
  경고 1건 가시화로 가독성 향상. CONTRIBUTING.md로 외부 기여자 온보딩 경로 확보.
- 문서 전용 변경(코드 무영향). 후속 과제: 현재 UI hero 스크린샷, runbook 현행화 후 Ollama 섹션
  추가 축약.
