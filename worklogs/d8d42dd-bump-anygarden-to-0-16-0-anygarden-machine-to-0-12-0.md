# chore(release): bump anygarden to 0.16.0, anygarden-machine to 0.12.0

- Commit: `d8d42dd` (d8d42dd)
- Author: Changyong Um
- Date: 2026-07-23
- PR: —

## Situation

두 워크스페이스 패키지가 마지막 릴리즈 태그 이후 게시되지 않은 변경을 누적했다.

- `packages/cluster` (PyPI 배포명 `anygarden`)는 `anygarden-v0.15.0` 이후 1개 병합 — 웹 UI 버전 표시 + PyPI 업데이트 감지·알림(#546, feat): `system` 모듈(version_service/store)·`/api/v1/system` 엔드포인트 3종·`version_checks` 테이블(migration 054)·Sidebar 버전/Admin System 페이지.
- `packages/machine` (배포명 `anygarden-machine`)는 `anygarden-machine-v0.11.0` 이후 2개 병합 — RegisterFrame `daemon_version` 보고 추가(#546, feat)와 systemd 유닛 PATH 결함 수정(#545, fix: 설치 시점 PATH 캡처 + 런타임 보정).
- `packages/agent`는 `anygarden-agent-v0.12.0` 이후 변경이 없다.

release 워크플로(`.github/workflows/release.yml`)는 태그 버전과 `pyproject.toml`의 `version`이 일치할 때만 게시하므로, 태그 push 전에 버전 bump가 선행되어야 한다.

## Task

- `packages/cluster/pyproject.toml`의 `version`을 `0.15.0` → `0.16.0`으로 올린다 (feat 포함 → minor).
- `packages/machine/pyproject.toml`의 `version`을 `0.11.0` → `0.12.0`으로 올린다 (feat #546 포함 → minor).
- `anygarden-agent`는 변경이 없으므로 bump/릴리즈에서 제외한다.
- 릴리즈와 무관한 미추적 파일은 커밋에 포함하지 않는다.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.15.0"` → `version = "0.16.0"`.
- `packages/machine/pyproject.toml:7` — `version = "0.11.0"` → `version = "0.12.0"`.
- 두 파일만 명시적으로 스테이징해 단일 커밋.

## Decisions

- bump 수준: cluster는 마지막 태그 이후 feat(#546) 포함이라 minor(0.16.0). machine은 fix(#545)만이 아니라 daemon_version 보고라는 신규 기능(#546, feat)을 함께 포함하므로 patch(0.11.1)가 아닌 minor(0.12.0)로 결정 — 사용자 확인을 거쳐 확정.
- agent 제외: `anygarden-agent-v0.12.0..HEAD -- packages/agent`가 공집합이라 릴리즈 대상 아님.
- `uv.lock`은 미추적(gitignore, CI가 `uv sync`로 재생성)이라 bump에 포함하지 않음 — #544/#542 등 기존 bump 커밋과 동일.
- main 반영은 프로젝트 컨벤션(PR + squash merge)에 맞춰 PR 경유로 진행하고, 머지된 main 커밋에 `anygarden-v0.16.0`·`anygarden-machine-v0.12.0` 태그를 push한다.

## Result

`anygarden` 배포 버전이 0.16.0, `anygarden-machine`이 0.12.0으로 표기된다. 이 커밋이 main에 머지되고 두 태그가 push되면 release 워크플로가 (cluster는 프론트엔드 빌드 →) sdist+wheel 빌드 → GitHub Release(릴리즈노트 자동생성) → PyPI 게시(Trusted Publishing/OIDC)까지 각각 자동 수행한다. 태그 push는 후속 단계.
