# Release operations runbook

This runbook is the operator-facing companion for `ANY-7` and the release gate.

## 배포 실행 전 체크리스트

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
