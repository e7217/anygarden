# Phase 6 release-gate and real-engine smoke acceptance plan

Status: contract aligned to task #39. This document deliberately does not
authorize a live model call; only the protected manual release workflow may do
so after its external controls have been approved.

Task: task #40

## Separation of test lanes

| Lane | Purpose | Default | External side effects |
| --- | --- | --- | --- |
| Browser release gate | Exercise the production frontend router with local REST fixtures. | Required on every exact head. | None: Vite binds loopback only; every API response is a test fixture. |
| Server/WS integration | Cover persistence, authorization, lifecycle, and reconnect behaviour. | Existing Python suites. | Isolated SQLite and local process fixtures only. |
| Real-engine smoke | Verify that one explicitly enabled engine can return a bounded, text-only response through the release harness. | Disabled unless the approved opt-in and budget guard are both present. | One provider request at most; no workspace, tool, filesystem, deployment, or external write. |

The browser gate must never use a Python service, database, daemon, or real
engine. A real-engine result cannot make a failed deterministic browser gate
acceptable.

## Current baseline and gap

`packages/cluster/frontend/e2e/auth.spec.ts` is a deterministic Vite-only
Playwright suite: it stubs every `/api/v1/**` route used by the login flow and
currently proves successful and failed sign-in behaviour.  CI installs
Chromium, runs `npm run test:e2e`, and uploads failure evidence.

`packages/cluster/tests/test_e2e_real_conversation.py` is a pre-Phase-6 manual
slow test. It makes two live `codex exec` calls, runs a five-turn conversation,
stores output temporarily, prints the model response, and has no explicit
budget/opt-in/evidence policy. It is **not** an eligible release gate and must
not be run by this task.

## Required deterministic browser release gate

The implementation must add/retain an exact-head command which:

1. runs with pinned dependencies and Chromium on CI;
2. starts only Vite on `127.0.0.1` and has no dependency on a live API,
   database, daemon, or model credential;
3. intercepts all API calls used by the journey locally, with unexpected
   requests failing the scenario rather than reaching a network service;
4. verifies the release-facing entry journey (authentication success and
   failure, session persistence, and the release-banner/safe-state surface
   chosen by the implementation);
5. emits Playwright trace/screenshot/video only on test failure and keeps those
   artifacts free of real credentials or model output;
6. is mandatory on the implementation PR's exact head and must pass before a
   release GO.

## Proposed real-engine smoke contract

Task #40 will accept an implementation only if all of the following are
executable and covered by a hermetic test:

- **Protected manual entrypoint:** this is `workflow_dispatch` only, never a
  PR/push trigger. It accepts only the default branch's exact current main SHA
  and rejects fork/PR refs, `pull_request_target`, arbitrary ref inputs, and
  arbitrary prompt inputs.
- **Preflight block:** a missing/invalid low-privilege credential, model,
  vendor egress allowlist, daily provider-side spend cap, or CI isolation is
  `BLOCKED_CONFIGURATION`, not pass or skip. The runner must not spawn an
  engine before every preflight has succeeded.
- **Single bounded invocation:** at most one model call per invocation and one
  static prompt. The prompt asks for a fixed, text-only acknowledgement and
  contains no user data, repository content, secrets, paths, task content, or
  external instructions. The exact accepted response is `ANYGARDEN_SMOKE_OK`,
  output is capped at 256 bytes, retry count is zero, and the wall-clock cap is
  60 seconds.
- **Read-only isolation:** the once-only empty working directory has no repo or
  host-home mount. `codex exec` uses `--ephemeral`, an explicit approved
  low-cost model/minimal effort, `sandbox_mode=read-only`, and
  `approval_policy=untrusted`. Tool or approval requests fail immediately;
  MCP, workspace attach, deployment, and write permissions are unavailable.
- **Network and cost guard:** only the approved vendor egress path may be used.
  A human-approved provider test account plus vendor-enforced one-request/day
  spend cap are mandatory before the subprocess starts.
- **Timeout and stop:** the whole invocation has a hard wall-clock limit of 60
  seconds. On timeout/cancellation the runner terminates the complete child
  process group with TERM, permits only a short grace period, then KILLs it and
  deletes only its own temporary fixture. It never retries.
- **Secret/output handling:** credentials stay in the process environment and
  are never printed or persisted. The response remains in memory only. Evidence
  stores only the exact SHA, workflow run, engine/model label, allowlisted
  outcome, duration, fixed-input hash, and output length/hash — never raw
  prompt, output, stderr, command line, temporary file, or environment.
- **Release semantics:** browser CI must be green and the protected manual
  smoke must PASS for the same release-candidate SHA. `FAIL`, `TIMEOUT`,
  `BLOCKED_CONFIGURATION`, stale SHA, or missing smoke evidence blocks release;
  no deployment belongs to this task.

## Acceptance matrix

| Case | Expected result | Evidence |
| --- | --- | --- |
| Browser fixture journey | Playwright pass; no external API/LLM | test result and failure-only local artifacts |
| Browser unexpected API | Deterministic test failure | route fixture error; no outbound request |
| Missing preflight control | `BLOCKED_CONFIGURATION`, no subprocess/provider request | allowlisted status only |
| Missing/invalid budget or engine policy | fail closed before subprocess | allowlisted denial code |
| Approved smoke response | one bounded, read-only request; fixed response predicate pass | redacted digest/status/duration |
| Timeout/cancellation | child process group terminated; temp fixture removed | timeout code/duration, no output |
| Unsafe output/error text | raw text absent from logs/report/artifacts | redaction regression |
| Exact-head rerun | browser + configured release profile results reproduced on PR head | SHA, commands, counts, no-write attestation |

## Exact-head QA handoff

When task #39 has frozen the contract and task #41 publishes a runnable
implementation SHA, QA will:

1. apply the corresponding regression cases without supplying a live credential;
2. run the deterministic browser gate, relevant frontend/unit tests, and the
   fail-closed real-engine cases;
3. run the live smoke only in the protected approved release environment; if a
   preflight control is absent, report `BLOCKED_CONFIGURATION` and block the
   release rather than treating it as pass;
4. record exact SHA, commands, pass/fail/not-run state, and proof that external
   workspace writes and deployment actions were zero; and
5. issue GO only if required hosted checks, independent review, and this matrix
   are all green on that exact head.
