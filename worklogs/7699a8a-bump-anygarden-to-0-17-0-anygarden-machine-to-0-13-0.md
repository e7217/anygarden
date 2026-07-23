# chore(release): bump anygarden to 0.17.0, anygarden-machine to 0.13.0

- Commit: `7699a8a` (7699a8a)
- Author: Changyong Um
- Date: 2026-07-24
- PR: —

## Situation

두 워크스페이스 패키지가 마지막 릴리즈 태그 이후 게시되지 않은 변경을 누적했다.

- `packages/cluster` (배포명 `anygarden`)는 `anygarden-v0.16.0` 이후 1개 병합 — #550 서버 주도 machine 자동 업데이트의 cluster 측: `POST /api/v1/machines/{id}/update` 엔드포인트, `Machine.update_status/update_error/update_started_at`(migration 055), `self_update_result` WS 수신 + 재등록 success 확정, AdminMachines [Update] 버튼·상태 UI (feat).
- `packages/machine` (배포명 `anygarden-machine`)는 `anygarden-machine-v0.12.0` 이후 1개 병합 — #550의 machine 측: 자기소유 venv 매니페스트(`install_manifest`)·self-update primitive(`updater`)·`anygarden-machine update`/`bootstrap` CLI·`scripts/install.sh`·`SelfUpdate(Result)Frame`·데몬 `_handle_self_update` (feat).
- `packages/agent`는 `anygarden-agent-v0.12.0` 이후 변경이 없다.

release 워크플로(`.github/workflows/release.yml`)는 태그 버전과 `pyproject.toml`의 `version`이 일치할 때만 게시하므로, 태그 push 전에 버전 bump가 선행되어야 한다.

## Task

- `packages/cluster/pyproject.toml`의 `version`을 `0.16.0` → `0.17.0`으로 올린다 (feat 포함 → minor).
- `packages/machine/pyproject.toml`의 `version`을 `0.12.0` → `0.13.0`으로 올린다 (feat 포함 → minor).
- `anygarden-agent`는 변경이 없으므로 bump/릴리즈에서 제외한다.
- 릴리즈와 무관한 미추적 파일은 커밋에 포함하지 않는다.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.16.0"` → `version = "0.17.0"`.
- `packages/machine/pyproject.toml:7` — `version = "0.12.0"` → `version = "0.13.0"`.
- 두 파일만 명시적으로 스테이징해 단일 커밋.

## Decisions

- bump 수준: 두 패키지 모두 마지막 태그 이후 #550이라는 신규 기능(feat)을 포함하므로 minor(0.17.0 / 0.13.0)로 semver 적용. #550은 cluster·machine 양쪽을 건드려 두 배포명 모두 릴리즈 대상이다.
- agent 제외: `anygarden-agent-v0.12.0..HEAD -- packages/agent`가 공집합이라 릴리즈 대상 아님.
- `uv.lock`은 미추적(gitignore, CI가 `uv sync`로 재생성)이라 bump에 포함하지 않음 — 기존 bump 커밋과 동일.
- main 반영은 PR + squash merge, 머지된 main 커밋에 `anygarden-v0.17.0`·`anygarden-machine-v0.13.0` 태그를 push한다.
- 운영 주의: 서버 주도 self_update 코드는 anygarden-machine 0.13.0부터 들어가므로, 각 머신이 최소 0.13.0 이상으로 (한 번은 수동/부트스트랩으로) 올라와야 서버 트리거가 실제로 동작한다.

## Result

`anygarden` 배포 버전이 0.17.0, `anygarden-machine`이 0.13.0으로 표기된다. 이 커밋이 main에 머지되고 두 태그가 push되면 release 워크플로가 (cluster는 프론트엔드 빌드 →) sdist+wheel 빌드 → GitHub Release(릴리즈노트 자동생성) → PyPI 게시(Trusted Publishing/OIDC)까지 각각 자동 수행한다. 태그 push는 후속 단계.
