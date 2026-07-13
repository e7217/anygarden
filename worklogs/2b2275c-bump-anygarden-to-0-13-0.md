# chore(release): bump anygarden to 0.13.0

- Commit: `2b2275c` (2b2275c8ec50480359b912e323d6d2c2b03d7f5a)
- Author: Changyong Um
- Date: 2026-07-13T09:20:23+09:00
- PR: —

## Situation

`packages/cluster` (PyPI 배포명 `anygarden`)는 마지막 릴리즈 태그 `anygarden-v0.12.0` 이후 5개 커밋이 쌓였다 — 에이전트 응답불가 관찰성·원클릭 복구(#517), 채팅 시각 지난 날짜 표기(#513), participant 유일성·검색 FTS 자가치유(#521), scheduler self-MCP 토큰 커밋(#511), 타임스탬프 통일(#515). 이 변경들은 아직 PyPI에 게시되지 않았다. release 워크플로(`.github/workflows/release.yml`)는 태그 버전과 `pyproject.toml`의 `version`이 일치할 때만 게시하므로, 태그를 밀기 전에 버전 bump가 선행되어야 한다.

## Task

- `packages/cluster/pyproject.toml`의 `version`을 `0.12.0` → `0.13.0`으로 올린다.
- 새 기능(feat) 2건이 포함되므로 semver상 minor 증가를 적용한다.
- 릴리즈와 무관한 미추적 파일·`package-lock.json` 변경은 커밋에 포함하지 않는다.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.12.0"`을 `version = "0.13.0"`으로 수정.
- 해당 파일만 스테이징해 커밋(다른 워킹트리 변경 제외).

## Decisions

N/A — mechanical change. 버전 문자열 한 줄 변경. bump 수준(minor 0.13.0)은 마지막 태그 이후 feat 2건 포함이라는 근거로 사용자가 patch(0.12.1) 대신 선택했고, main 반영은 프로젝트 컨벤션(PR + squash merge)에 맞춰 PR 경유로 진행한다.

## Result

`anygarden` 배포 버전이 0.13.0으로 표기된다. 이 커밋이 main에 머지되고 `anygarden-v0.13.0` 태그가 push되면 release 워크플로가 프론트엔드 빌드 → sdist+wheel 빌드 → GitHub Release → PyPI 게시(Trusted Publishing/OIDC)까지 자동 수행한다. 태그 push는 아직 수행되지 않음(후속 단계).
