# Issue 588 implementation boundary

Base inspected: main `66235f332f6538bceb1591f1c8b12bde4dbbb7ef`.
Common local-node contract: PR 596 ADR 007 (initial handoff
`77807f94e45a0ed9a056147d7d0d475a10401f91`). The contract documents are owned by
issue #587; this change does not copy or alter them. Later contract head
`ac7667718f26a78e4a4b686011ee456ab074c9b5` retains these local-node boundaries.

PR 583 at `9678fe8793794b14b4872e23f6cbf7e7dd700410` remains separate. Its lifecycle
query batching, daemon report snapshot and participant index/migration are not
cherry-picked. In shared files this change only generalizes the lifecycle's
transport type and adds daemon spawn-drain tracking. PR 583's report/query paths
retain their own ownership and should be reviewed for merge conflicts when the
branches are integrated.

Owned here: CLI, app lifecycle, data-directory ownership/identity, local Machine
provisioning, MachineBus local delivery, shared authenticated machine-frame
handling, local ownership protection, and corresponding tests/runbook. The
public delivery bool still means acceptance only. Server startup cleanup now also
runs after partial failures. No product DB migration was added or run against a
live database.

Issue #589 owns runtime adapters, receipt/session storage, ChatClient removal and
invocation/result semantics. Issue #590 receives the existing `node_id` through
`app.state.node_owner.identity`; it owns peer trust/keys and does not regenerate
that identity. App integration remains owned here. The first integrated mode is
explicitly single-worker with no reload; PM confirmed multi-worker IPC is not a
requirement for this version in task #13.
