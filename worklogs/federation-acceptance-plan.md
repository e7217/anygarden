# Two-node acceptance preparation — issue #594 / Raft task #14

## Evidence boundary

Baseline: `66235f332f6538bceb1591f1c8b12bde4dbbb7ef`, fresh origin/main on
2026-09-16. Sources: GitHub e7217/anygarden issues #586–594, read at preparation.
This checkpoint is an acceptance plan and runnable harness plumbing, **not a
federation implementation, product acceptance, or completion of issue #594**.
No real providers, live hosts, deployment, migration, or merge are exercised.

The fixture has two separate OS processes, SQLite files, node identities and
workspace directories. Communication is test-controller JSON over pipes; it is
not authenticated federation traffic. Fixture receipts are not product execution
receipts. No runtime executes, and a receipt count cannot prove absence of duplicate
file/API side effects. ACK loss means the controller reads then discards an ACK
from the simulated sender's perspective, after the receiver commits; it does not
model TCP/TLS behavior. Peer termination is a real local fixture process kill.

## Run the initial fixture

From repository root, on Linux with Python 3.12+ and no installed dependencies:

```sh
python3 -m unittest discover -s tests/federation_harness -v
```

Six self-tests cover separate resources, lost-ACK replay after receiver restart,
conflicting payload reuse/origin scoping, independent local receipt storage while
peer is down, concurrent duplicate delivery, and canonical contract payload preservation across restart. Child environment is explicitly
minimal, with no inherited provider credentials. Temporary databases are removed;
there are no network calls. These results must be labelled `fixture_only`.

Linux CI runs this exact command in the explicit
`Test federation fixture plumbing (Linux, no product E2E)` step before workspace
dependency installation. It does not rely on package pytest discovery.

Files owned by QA: `tests/federation_harness/`, this plan. Architecture owns
`docs/decisions/007-federated-node-contract.md` and `contracts/federation/v1/`.
The fixture's internal commands must never be promoted into a production API.

## Acceptance matrix — all product rows NOT RUN

| ID / dependency | Trigger / fault | Required observation and independent oracle |
|---|---|---|
| N01 #588 | Clean standalone startup, second supervisor, reload, partial startup failure | One execution owner; no orphan child; local message/task usable; actual process IDs + DB owner/lease evidence |
| N02 #588 | Two node processes with independent roots | Distinct stable identities, DBs and workspaces; one node write absent from unshared peer state; identity persists across restart |
| T01 #590 | Invite/accept; expired/reused/forged invite; wrong peer/version | Authenticated accepted peer only; rejection causes zero grant/new receipt/execution; check both databases |
| T02 #590/#593 | Unshared channel/agent, observer, removed member, archived room; search/autocomplete | Denied API and no UI disclosure; zero execution; never infer global rights from connected peer |
| C01 #591 | Text/thread/member sync in A-owned and B-owned channels | Same committed IDs/order/root linkage; authority verified per channel; local-only data absent |
| C02 #591 | Drop ACK after durable receive, duplicate event, same ID/different body, reorder/gap | Same payload produces one visible event; changed payload rejected; cursor cannot skip missing committed event; receipt and UI count agree |
| C03 #591 | Kill/restart sender and receiver, disconnect before and after durable receive | Persisted outbox replay; no fabricated ACK; recovery converges once; record exact crash boundaries |
| C04 #591/#588 | Disconnect channel authority while peer stays up | Shared changes pending/unconfirmed, never locally granted; separate local task continues; no automatic authority promotion |
| D01 #592/#589 | A delegates to B; B accepts then emits fake progress/success or known failure | Authority task → B Turn/Attempt → receipt trace; one terminal result with success/known failure/unknown distinct; runtime invocation ledger separately counted |
| D02 #592 | A/B race to claim one task using a barrier | One authoritative winner, loser never starts runtime; assert persisted owner and invocation IDs, not HTTP success alone |
| D03 #592 | Lost execution ACK; restart after runtime start but before receipt | Existing execution reconciled or explicit unknown; invocation ledger has no blind replay; no exactly-once external side-effect claim |
| D04 #592 | Cancel request before start / during execution / before result commit; delayed old-generation result | Cancellation request differs from stopped acknowledgement; terminal decision has one ordering authority; stale result cannot restore running/completed; process-tree state observed |
| D05 #590–592 | Revoke before queue drain / after acceptance / during execution / before delayed result | Replay re-authorized; new requests denied; stale credential/grant epoch fails; already-running behavior follows explicit contract; retain prior copies without claiming remote erasure |
| R01 #589 | Valid resume, expired session, partial-run nonzero, unsupported capability, timeout | Session scoped to node/agent/channel/thread/task/workspace; no broad nonzero→fresh retry; cancelled child tree stopped or unknown explicitly reported |
| U01 #593 | Browser invite/share/delegate/revoke and both outage types | Requested/accepted/running/result/confirmed states distinguishable; authority offline vs executor offline clear; refresh matches durable backend state |
| O01 #594 | Upgrade from prior version, backup/restore, reconnect, rollback | Identity/session continuity or explicit migration restriction; restored outbox does not resurrect revoked work; operational steps reproducible |

Auth, task state, cancellation, crypto, network, real process-tree cancellation,
UI and product crash recovery are **not** covered by current fixture self-tests.
Existing lifecycle/turn/room authorization tests remain regression prerequisites;
the new matrix does not replace them. PR #583–585 remain separate unmerged
changes at this baseline; integration must record their eventual inclusion.

## Contract questions / hookup requirements

Architecture acknowledged QA feedback in Raft `e863da51` and `6d3ab409`:
ACK only after authorized durable apply; replays require matching ID/body and
current authorization; unknown execution cannot be blindly replayed; task state
and delegation state are distinct. The initial v1 fields and cancel/result ordering are now supplied by PR #596.
The following remain required observations when connecting the product adapter:

- Request/event/receipt identity scopes and canonical payload digest rules.
- Authority sequence/cursor gap handling and durable ACK/replay decision surface.
- Authorization epoch and ordering of revoke vs queued execution vs late result.
- Task/Turn/Attempt/generation/session mapping and observable runtime invocation ID.
- Cancellation linearization point and allowed late-result states.
- Unknown execution reconciliation and which node may confirm final outcome.

This branch consumes the versioned fixtures/validator directly without schema forks.
Next add a product driver that starts two actual AnyGarden
instances with independent configuration and a deterministic fake runtime. Keep
fault injection in the transport/runtime boundary and expected states in these
acceptance assertions; do not implement business decisions inside the test driver.
A missing product adapter must be NOT RUN or fail the integration gate, never
silently substitute this fixture or report a skipped suite as acceptance PASS.

## Evidence and rollout of coverage

For each integrated scenario save commit + contract version, OS/Python/runtime
versions, node IDs, scenario/seed, injected fault and monotonic timestamps, request /
receipt / task / attempt IDs, state snapshots and invocation/completion counts.
Keep logs free of credential values and host-private paths. Use elapsed monotonic
time for first task and recovery; report success/failure/unknown counts with a
clear denominator. No synthetic fixture timing is an operational baseline.

1. Current checkpoint: six fixture self-tests; matrix and canonical contract hookup.
2. Contract checkpoint: canonical fixtures validated and payload preservation checked;
   binding actual product adapter fields remains NOT RUN.
3. Product checkpoint: actual two-node provider-free backend suite on an exact
   integrated commit, preserving existing permission/lease/generation regression.
4. Browser checkpoint: two browser contexts against those nodes; backend evidence
   linked to UI observations. Mock UI tests recorded separately.
5. Operational checkpoint: install/upgrade/restore guide tested in clean local
   isolation. Two real hosts and actual first runtime require separate scope and
   environment; historical Codex 0.146.0 fixture is not compatibility evidence for
   host 0.154.0. Issue #578 actual provider smoke remains a separate investigation.

## Canonical contract hookup (2026-09-16)

Source: PR #596 exact `7ff28be1709f037e6e549df3946deef4ea617fc2`.
Initial `77807f9` was cherry-picked as `24181c4`; its result-state correction
`7ff28be` was cherry-picked as `4a9a8f5`, both without modification. The seven
contract/ADR files match that source exactly. PR #595 currently includes this
unmerged dependency; it must not bypass the independent #596 review. After #596
is merged, refresh the QA branch against main and confirm that only QA/CI changes
remain in the PR diff. No target branch or PR has been merged by this work.

CI dependency order: fixture self-tests use Python stdlib and committed
`scenarios.json`; contract model validation runs after Install uv and before
workspace sync using `uv run --no-project contracts/federation/v1/check.py`.
The latter installs the script's pinned jsonschema dependency if uncached. Missing
contract files fail these steps rather than skipping or fetching another branch.

Local results: six fixture tests passed; the unchanged checker passed 47 command
scenarios / 164 decisions and 8 event scenarios / 18 decisions. The additional
fixture test transports the complete normal-completion command payloads, restarts
the receiver, replays them, and compares stored JSON to the canonical source.
It does not apply commands or establish authorization/execution correctness.
The contract checker is a separate in-memory reference-model check. Hosted CI
must be checked on the pushed head; local results do not imply hosted success.

The initial contract now specifies first-confirmed cancel/result ordering,
separate process_state, bounded late-result observation after cancellation,
session scoping including agent_id, succeeded/failed outcome separation, and
rejection releasing the reservation without a replay clearing a newer claim.
Known failed execution stays distinct from unknown execution and cancelled work.
Product durability, process termination,
TLS, session migration and real runtime compatibility remain NOT RUN.
