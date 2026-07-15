# chore(release): bump anygarden to 0.14.0, anygarden-agent to 0.11.2

- Commit: `8afbab4` (8afbab46a54e5b1fea277d9b0b7b6794701d1155)
- Author: Changyong Um
- Date: 2026-07-15T19:49:47+09:00
- PR: —

## Situation

두 워크스페이스 패키지가 마지막 릴리즈 태그 이후 게시되지 않은 변경을 누적했다.

- `packages/cluster` (PyPI 배포명 `anygarden`)는 `anygarden-v0.13.0` 이후 2개 커밋 — 머신 상세에 감지된 시스템 정보(호스트네임·IP·OS·CPU·RAM) 표시 + description 필드(#524, feat), 설정 API 시크릿의 직렬화-시점 레댁션 구조화(#529, refactor).
- `packages/agent` (배포명 `anygarden-agent`)는 `anygarden-agent-v0.11.1` 이후 2개 커밋 — 멀티에이전트 정체성·화자 귀속 결함 수정으로 LLM 프롬프트에 speaker 신원 주입(#539, fix), codex-cli에 system_prompt(자기정체성) turn-content 주입(#541, fix).
- `packages/machine`은 `anygarden-machine-v0.11.0` 이후 변경이 없다.

release 워크플로(`.github/workflows/release.yml`)는 태그 버전과 `pyproject.toml`의 `version`이 일치할 때만 게시하므로, 태그 push 전에 버전 bump가 선행되어야 한다.

## Task

- `packages/cluster/pyproject.toml`의 `version`을 `0.13.0` → `0.14.0`으로 올린다 (feat 포함 → minor).
- `packages/agent/pyproject.toml`의 `version`을 `0.11.1` → `0.11.2`로 올린다 (fix만 → patch).
- `anygarden-machine`은 변경이 없으므로 bump/릴리즈에서 제외한다.
- 릴리즈와 무관한 미추적 파일·`package-lock.json` 변경은 커밋에 포함하지 않는다.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.13.0"` → `version = "0.14.0"`.
- `packages/agent/pyproject.toml:7` — `version = "0.11.1"` → `version = "0.11.2"`.
- 두 파일만 명시적으로 스테이징해 단일 커밋(다른 워킹트리 변경 제외).

## Decisions

- bump 수준: cluster는 마지막 태그 이후 feat(#524) 포함이라 minor(0.14.0), agent는 fix만이라 patch(0.11.2)로 semver 적용.
- machine 제외: `anygarden-machine-v0.11.0..HEAD -- packages/machine`가 공집합이라 릴리즈 대상 아님.
- main 반영은 프로젝트 컨벤션(PR + squash merge)에 맞춰 PR 경유로 진행하고, 머지된 main 커밋에 `anygarden-v0.14.0`·`anygarden-agent-v0.11.2` 태그를 push한다.

## Result

`anygarden` 배포 버전이 0.14.0, `anygarden-agent`가 0.11.2로 표기된다. 이 커밋이 main에 머지되고 두 태그가 push되면 release 워크플로가 (cluster는 프론트엔드 빌드 →) sdist+wheel 빌드 → GitHub Release(릴리즈노트 자동생성) → PyPI 게시(Trusted Publishing/OIDC)까지 각각 자동 수행한다. 태그 push는 후속 단계.
