# chore(release): anygarden 0.10.0, anygarden-agent 0.9.0, anygarden-machine 0.9.0

- Commit: `89b3a0e`
- Author: Changyong Um
- Date: 2026-06-19
- PR: (release)

## Situation

ADR-006 신뢰성 하드닝 Wave 0~2 + 잔여 항목이 11개 PR(#446~#466)로 main에 병합됐고, 3개 패키지 모두 마지막 릴리즈 태그 이후 실질 변경(cluster 19커밋·agent 6·machine 2)이 쌓였다. PyPI에 배포해 사용자가 받을 수 있게 해야 한다.

## Task

3개 패키지 버전을 bump하고 태그를 푸시해 PyPI Trusted Publishing(release.yml, tag 트리거)으로 배포. 버전·대상은 사용자 확인됨(minor, 3개 전부).

## Action

- `packages/cluster/pyproject.toml` 0.9.1 → **0.10.0**
- `packages/agent/pyproject.toml` 0.8.0 → **0.9.0**
- `packages/machine/pyproject.toml` 0.8.1 → **0.9.0**
- `uv.lock`은 gitignored라 미커밋(CI `uv sync`가 재생성). 상호 의존(`anygarden-machine>=0.8`/`anygarden-agent>=0.8`)은 하한 제약이라 미수정(호환).
- 후속: 태그 `anygarden-v0.10.0` / `anygarden-agent-v0.9.0` / `anygarden-machine-v0.9.0` 푸시 → release.yml가 pyproject 버전 일치 검증 후 빌드(cluster는 프론트 SPA 포함)·배포.

## Decisions

- **minor bump (patch 아님)** — 신뢰성 기능 추가(예산·goal CAS·reaper·queue/retry·task_blockers·telemetry 등)라 0.x minor가 적절. 사용자 확인.
- **3개 동시 릴리즈** — agent↔cluster가 LifecycleFrame 프로토콜(신규 outcome/telemetry 필드)을 공유하므로 버전 정합을 위해 함께 배포. (필드는 additive라 하위호환되나 동시 배포가 깔끔.)
- **cross-dep 하한 유지** — `>=0.8`이 0.9.0을 만족, additive 프로토콜이라 강제 상향 불필요(최소 변경).

## Result

- 3개 pyproject 버전 bump 커밋. 태그 푸시로 release.yml 트리거 예정 — 빌드/배포 결과는 워크플로에서 확인.
