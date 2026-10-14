# ci: remove the Engine Smoke release gate (#835)

- Commit: `a8dfeda` (a8dfedaf18e221435cda36ebdf436338cce34c69)
- Author: Changyong Um
- Date: 2026-10-14T16:23:26+09:00
- PR: —

## Situation

#566 made every `anygarden-v*` tag wait for a successful `Engine Smoke` run on the same commit. The smoke made one codex call inside a pinned container on a protected self-hosted runner (`anygarden-release-smoke`), behind an egress proxy, with seven or eight environment variables and a paid `ANYGARDEN_SMOKE_OPENAI_API_KEY`. It ran nine times between 2026-08-06 and 2026-09-23 and never passed; the last run failed with `AUTH_REJECTED`. No release has shipped since 0.18.0 (2026-07-24), which held back 0.19.0 and the #832 agent merge.

## Task

- Unblock releases without weakening the deterministic gate: Vitest, Playwright and the production build
- Remove the smoke code and workflows entirely instead of leaving them unused
- Record how real engines are checked from now on

## Action

- `.github/workflows/release.yml`: removed the same-SHA smoke step, the top-level `actions: read` permission it needed, and the `release-gate` checkout's `fetch-depth: 0`. Renamed the job to `Browser gate`
- Deleted `.github/workflows/engine-smoke.yml`, `.github/workflows/publish-engine-smoke-image.yml`, `.github/engine-smoke/Dockerfile`, `packages/cluster/scripts/engine_smoke_{gate,runner_validation,stub_validation}.py` and their three test files. None of the tests carried `@pytest.mark.req`, so requirement coverage is unaffected
- `packages/cluster/tests/test_release_gate_workflows.py`: kept only the browser gate assertion
- `docs/runbook/release-gate.md`: rewritten. Before tagging, run one `codex-cli` agent and one `pi-cli` agent on an integrated node built from the release commit
- `packages/cluster/frontend/playwright.config.ts`: comment wording

## Decisions

- Options weighed: (A) renew the key and keep the gate; (B) drop it from the release requirements but keep the workflow for manual runs; (C) remove it entirely
- C won. The gate never passed, so in practice it guarded nothing and only stopped releases. B would leave about 2,100 lines plus a self-hosted runner, a proxy, an image and secrets that nobody runs and that would keep decaying. Real engines are used daily on the live node and checked more broadly with live scenario verification
- #725 had earlier rejected a local real-engine E2E because "Engine Smoke owns it". That ownership now moves to the manual pre-tag check in the runbook
- Revisit if releases gain other operators or deploy targets. In that case, a live-node scenario check in the release checklist is the likelier replacement than a CI canary

## Result

- Release tags are gated by the browser gate and build only
- Cluster suite with Node on PATH: 3558 passed, 2 skipped (Windows-only). That is 139 fewer than before: the 135 smoke tests plus four of the five release-gate assertions
- Pending, after merge and with confirmation: delete the `release-smoke` environment (variables and secret), the GHCR smoke image and the runner registration; the operator decommissions the runner VM
