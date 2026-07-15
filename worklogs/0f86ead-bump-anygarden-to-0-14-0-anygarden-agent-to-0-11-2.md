# chore(release): bump anygarden to 0.14.0, anygarden-agent to 0.11.2

- Commit: `0f86ead`
- Author: Changyong Um
- Date: 2026-07-15
- PR: —

## Situation

세 패키지의 현재 버전(`anygarden-v0.13.0`, `anygarden-agent-v0.11.1`, `anygarden-machine-v0.11.0`)은 이미 태그·게시된 상태다. 각 릴리즈 태그 이후 소스가 더 쌓인 패키지가 있어, 그 변경을 게시하려면 버전 bump가 선행되어야 한다(`release.yml`은 태그 버전과 `pyproject.toml` version 일치 시에만 게시).

- `anygarden`(cluster) 0.13.0: 태그 `anygarden-v0.13.0`(56c1d51) 이후 feat(#524 머신 상세 시스템정보 표시) + refactor(#529 설정 API 시크릿 직렬화-시점 레댁션)가 쌓임 — 미게시.
- `anygarden-agent` 0.11.1: 태그 `anygarden-agent-v0.11.1`(d48105a) 이후 fix 2건(#539 멀티에이전트 화자 신원 주입, #541 codex-cli 자기정체성 turn-content 주입) — 미게시.
- `anygarden-machine` 0.11.0: 태그 `anygarden-machine-v0.11.0`(4b7e2ec) 이후 변경 없음 — 이미 게시됨, 새 태그 불필요.

## Task

- `packages/cluster/pyproject.toml` version `0.13.0` → `0.14.0` (feat 포함 → semver minor).
- `packages/agent/pyproject.toml` version `0.11.1` → `0.11.2` (fix만 → semver patch).
- `packages/machine`는 버전 유지(0.11.0).
- 릴리즈와 무관한 미추적 파일·`package-lock.json`은 커밋 제외.

## Action

- `packages/cluster/pyproject.toml:7` — `0.13.0` → `0.14.0`.
- `packages/agent/pyproject.toml:7` — `0.11.1` → `0.11.2`.
- 두 파일만 스테이징해 `chore(release)` 커밋.

## Decisions

- cluster bump 수준: 마지막 태그 이후 feat 1건 포함이라는 근거로 minor(0.14.0). agent: fix만이라 patch(0.11.2). machine: 변경 없어 유지(이미 게시됨).
- 새로 생성할 태그는 `anygarden-v0.14.0`, `anygarden-agent-v0.11.2` 2개다(machine은 이미 `anygarden-machine-v0.11.0`으로 게시되어 제외).

## Result

cluster/agent 배포 버전이 각각 0.14.0 / 0.11.2로 표기된다. 위 2개 태그를 push하면 release 워크플로가 프론트엔드 빌드(cluster) → sdist+wheel → GitHub Release(--generate-notes) → PyPI 게시(Trusted Publishing/OIDC)를 자동 수행한다.
