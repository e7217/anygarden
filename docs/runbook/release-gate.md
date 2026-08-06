# Release gate

Every release tag is blocked until two independent checks pass on the tag's
exact commit.

1. The tag workflow reruns frontend Vitest, deterministic Chromium Playwright,
   and the production build. These tests use only local fixtures; they never
   receive a database, engine credential, or external provider connection.
2. A successful `Engine Smoke` `workflow_dispatch` run must already exist for
   the same default-branch SHA. Missing, failed, timed-out, or stale smoke runs
   block the tag before any build, GitHub Release, or PyPI publication occurs.

## Protected engine smoke

The live job is intentionally unusable by default. Configure it only after the
repository owner approves the provider/test account, a vendor-enforced daily
limit of one call, and the egress isolation.

The `release-smoke` GitHub environment must have a reviewer protection rule and
one custom deployment branch policy named `main`. Configure these environment
values:

- `ANYGARDEN_SMOKE_APPROVED=true`
- `ANYGARDEN_SMOKE_BUDGET_POLICY=vendor-daily-cap-1`
- `ANYGARDEN_SMOKE_EGRESS_POLICY=vendor-only`
- `ANYGARDEN_SMOKE_CREDENTIAL_SCOPE=low-privilege-test-only`
- `ANYGARDEN_SMOKE_MODEL=<approved low-cost model>`
- `ANYGARDEN_SMOKE_CONTAINER_IMAGE=ghcr.io/...@sha256:<digest>`
- secret `ANYGARDEN_SMOKE_OPENAI_API_KEY` for the dedicated test account

The protected self-hosted runner must carry the labels `linux` and
`anygarden-release-smoke`, Docker, and a preconfigured Docker network named
`anygarden-smoke-egress`. The pinned container must include Python and Codex.
The workflow mounts only the reviewed runner script and an evidence directory;
it does not mount the repository or host home.

The runner makes exactly one ephemeral, read-only, no-retry invocation with a
constant canary prompt, minimal effort, a 60-second process timeout, and a
256-byte response limit. A tool or approval request fails the smoke. Evidence
contains only the exact SHA/run, engine/model version, result code, duration,
and input/output length and hashes. Raw prompts, responses, credentials,
sessions, stderr, and temporary files are not retained.

The hosted preflight receives no provider credential and checks only non-secret
configuration. The credential is injected and checked only inside the protected
self-hosted job's isolated container. If either stage lacks a prerequisite, it
writes redacted `BLOCKED_CONFIGURATION` evidence and exits non-zero. Do not
reinterpret that result as a skip or a pass.
