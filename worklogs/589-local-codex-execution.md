# #589 — Local Codex execution and durable receipts

Baseline: main `66235f332f6538bceb1591f1c8b12bde4dbbb7ef`.
Contract: PR596 `ac7667718f26a78e4a4b686011ee456ab074c9b5` (execution contract from `7ff28be`).

Added a ChatClient-independent local execution manager, persistent invocation
receipts/events, scoped native sessions, current authorization checks and a
Codex CLI 0.154.0 subprocess boundary. The caller supplies the materialized
workspace, runtime home and environment. Session work is serialized; duplicate
execution IDs are replayed only with unchanged content and current permission.
Cold uncertain launches remain unknown. Process-tree cancellation is confirmed
before a terminal cancellation/failure receipt. The old Codex route no longer
turns arbitrary resumed nonzero exits into a fresh invocation.

Provider-free coverage uses the actual product manager with fake subprocesses,
plus the installed native 0.154.0 binary against a loopback response fixture in
an isolated network namespace. No external provider, credentials, deployment,
production database migration or merge was used. Native fresh and resumed turns
produced exactly two loopback requests. This does not establish real model access
or actual two-node dispatch/result publication.

Integration contract, limitations and reproduction commands:
`packages/agent/docs/local-execution.md`.

Final local evidence: 550 agent tests passed, including 28 execution regressions;
changed-file Ruff and whitespace checks passed. Existing suite emits deprecated
WebSocket and unrelated mock-cleanup warnings. The initial full-suite attempt
accidentally loaded an older installed cluster package; rerunning with all three
source packages from this worktree resolved the protocol-field mismatch.
