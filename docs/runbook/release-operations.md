# Release operations runbook

This runbook is the operator-facing companion for `ANY-7` and the release gate.

## 배포 수용 조건 — daemon 우선 업그레이드 (차단 조건)

**이것은 권고가 아니라 릴리스·배포의 수용 조건이다.** 충족되지 않으면 진행하지 않는다.

- cluster(`anygarden`)를 배포하기 전에, 또는 **동시에**, 원격 machine daemon을
  cluster가 요구하는 `anygarden-machine` floor 이상으로 올려야 한다.
  현재 floor는 `packages/cluster/pyproject.toml`의 `server`/`machine`/`dev` extras에 있다.
- 역순(cluster 먼저)은 **서버는 fencing 중이라고 믿고 daemon은 아닌 구간**을 만든다.
  floor 미만 daemon은 stop tombstone을 영속 high-watermark로 들지 않으므로, stop 뒤
  다른 머신에서 start하면 원래 generation이 계속 살아 있을 수 있다(#581이 막은 split-brain).
- **legacy generation-advance 한계**: cluster가 한 번 generation을 올린 뒤로 floor 미만
  daemon은 해당 에이전트에 대해 계약 밖이다. 서버는 그 daemon의 보고를 무시하지만,
  **프로세스와 그 부작용까지 멈추지는 못한다.** 즉 이 조건 위반은 서버 재시작으로
  복구되지 않는다.
- daemon 업그레이드 수단: 각 호스트에서 `anygarden machine update`, 또는 웹 UI의
  **Admin → Machines → Update**.

### 자동화가 강제하는 범위 (fail closed)

`release.yml`의 `publish-package`는 cluster 태그(`anygarden-v*`)에서
**"Require the machine floor to be publishable first"** 로 차단한다.

- cluster가 요구하는 floor를 `pyproject.toml`에서 읽어, 그 버전의
  `anygarden-machine`이 **PyPI에 이미 게시되어 있는지** 확인한다. 없으면 publish를 막는다.
- floor를 읽지 못하거나 여러 개가 나오거나 인덱스에 도달하지 못하면 **모두 차단**한다.
  통과는 명시적 확인에만 주어진다.

**자동화가 덮지 못하는 것**: 이 게이트는 "운영자가 daemon을 먼저 올릴 수 *있는* 상태"만
보장한다. 실제로 원격 daemon이 올라갔는지는 CI에서 관측할 수 없으므로 **운영자가 위 수용
조건으로 확인해야 한다.** 게이트 통과를 롤아웃 완료의 증거로 읽지 말 것.

## 배포 실행 전 체크리스트

- **위 daemon 우선 업그레이드 수용 조건이 충족되었다** (충족 전에는 진행 금지).

- Tag targets are correct: anygarden-v*, anygarden-machine-v*, anygarden-agent-v*.
- CI is green for the exact release commit in `ci.yml`.
- Frontend checks in release gate pass: Vitest, Playwright release E2E, production build.
- Engine smoke gate pass is required and evidence is `PREFLIGHT_PASS` for exact SHA.
- `release-smoke` environment has reviewer protection and main-only branch policy.
- `ANYGARDEN_SMOKE_OPENAI_API_KEY` is only defined in the live canary scope.
- Container digest is pinned in `vars.ANYGARDEN_SMOKE_CONTAINER_IMAGE`.
- Smoke canary model is test-approved and budget policy is one-call cap.
- Preflight evidence artifact exists and matches the current SHA.

## 환경 격리 정책

- `engine-smoke.yml` live canary uses `self-hosted` runner labels `linux` and `anygarden-release-smoke`.
- Docker container runs `--read-only --cap-drop ALL --security-opt no-new-privileges`.
- Network policy is fixed to `anygarden-smoke-egress`.
- Runtime is forced to non-persistent directories `HOME=/tmp/home`, `CODEX_HOME=/tmp/codex`.
- `/tmp` and `/work` are mounted as tmpfs with explicit `noexec,nosuid` mode.
- `engine_smoke_gate.py` enforces `workflow_dispatch`, SHA pinning, protected environment, and fixed runtime env vars.
- The reviewed runner is mounted read-only; the evidence file is the only
  writable bind mount exposed to the canary.

## 시크릿 접근 감사

- Non-secret variables in scope: `ANYGARDEN_SMOKE_APPROVED`, `ANYGARDEN_SMOKE_BUDGET_POLICY`, `ANYGARDEN_SMOKE_EGRESS_POLICY`, `ANYGARDEN_SMOKE_PROXY_URL`, `ANYGARDEN_SMOKE_CREDENTIAL_SCOPE`, `ANYGARDEN_SMOKE_CONTAINER_IMAGE`, `ANYGARDEN_SMOKE_MODEL`.
- Secret in scope: runtime-only `ANYGARDEN_SMOKE_OPENAI_API_KEY`.
- Preflight job must not reference `OPENAI_API_KEY`.
- Live canary step must only pass `OPENAI_API_KEY` into container runtime.
- Child runtime env in `engine_smoke_gate.py` includes only allowlisted proxy and runtime keys.
- `secret-audit` artifact (`engine-smoke-secret-audit-<sha>`) must exist after preflight.
  This artifact is **workflow-scope evidence**: it records which secrets and variables the
  workflow declares in scope for that SHA. It is **not** proof that a secret was
  dynamically absent from any process at runtime — it cannot observe the child process
  environment. Runtime absence is enforced separately by the allowlist in
  `engine_smoke_gate.py` and by the preflight job not referencing `OPENAI_API_KEY`.

## 임시 자원 정리

- Preflight cleanup must remove temporary directories after evidence upload.
- Live cleanup must remove `${RUNNER_TEMP}/engine-smoke-evidence`.
- Live cleanup must remove `${RUNNER_TEMP}/engine-smoke-runner`.
- Artifacts should retain only redacted JSON evidence and no prompt/response text.

## 모니터링 알람 정의

- Alert `release_gating_blocked_by_smoke`: release-gate fails with `Release blocked`.
- Alert `engine_smoke_failure`: `engine-smoke` run result is `FAIL*` or `BLOCKED_CONFIGURATION`.
- Alert `smoke_secret_audit_missing`: secret-audit artifact is absent or malformed.
- Alert `release_publish_failed_after_build`: `publish-package` fails after successful build.
- Alert escalation chain: on-call engineer, then platform lead, then security owner for repeated smoke blocks.

## Runbook pointers

- Release gate reference: `docs/runbook/release-gate.md`.
- Smoke contract code: `packages/cluster/scripts/engine_smoke_gate.py`.
- Smoke workflows: `.github/workflows/engine-smoke.yml`, `.github/workflows/publish-engine-smoke-image.yml`.
- Release orchestration: `.github/workflows/release.yml`.
