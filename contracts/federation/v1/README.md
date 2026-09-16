# Federation v1 contract fixtures

Design companion for [ADR 007](../../../docs/decisions/007-federated-node-contract.md)
and issue #587. No service, database migration or provider call is implemented.

Run from the repository root:

```sh
uv run --no-project contracts/federation/v1/check.py
```

The script's inline dependency pins `jsonschema==4.23.0`; first invocation may
fetch validation packages. Validation itself opens no sockets and launches no
runtime. Python 3.11+ is required. Repository dependency/lock files are unchanged.

- `envelope.schema.json`: closed command union; UUID identity, version 1,
  authority/channel/grant binding, selected text and task/execution references.
- `receipt.schema.json`: authority commit receipt. `state` is delegation state;
  `process_state` is separate execution evidence, not an inferred task outcome.
  `task_status` projects the existing Task enum (null for messages). Known failed
  result and unknown execution are distinct; rejection releases the reservation.
- `event.schema.json`: ordered committed command + receipt, with schema IDs
  resolved locally by `check.py` (never fetched over the network).
- `scenarios.json`: complete input context, command vectors and expected codes;
  consumers should use these language-neutral fixtures, not the reference
  implementation as production security code. Each scenario starts fresh.
  `context_updates` represents authoritative fixture state changes, not wire
  fields. `transport_node` is verified connection identity, never trusted input.
  Event vectors reference the emitted events of `normal_completion` by index.
- `check.py`: in-memory authority and follower reference models. Rejects changed
  ID reuse, fresh authorization failures, stale revision/execution, late result
  after cancellation, out-of-order events and changed-event replay. Rejected
  commands cannot mutate task/message/event state. A late authorized result
  may add a bounded observation receipt, without storing/publishing result text.

At authority unavailable, the model returns `AUTHORITY_UNAVAILABLE`. A client's
unconfirmed local outbox is not a committed delegation. Local independent
channels continue under their own authority, exercised by the later QA harness.
An `unknown` outcome blocks automatic execution/retry. This checker does not
actually launch processes and therefore cannot prove absence of side effects.

Fixture outcomes: 47 command scenarios / 164 decisions; 8 event scenarios /
18 decisions. Additional invalid JSON cases reject duplicate keys, NaN, Infinity
and float encodings. Command UTF-8 size is limited to 64 KiB by the model; each
text field is at most 16,384 characters. Transport must enforce its own bounded
read before parsing. JSON schemas do not replace fresh authorization/CAS.

Still NOT RUN: actual product routes, DB transaction/receipt durability, concurrent
CAS, TLS/pinned identities, invitation consumption, session migration, process
reaping, runtime CLI compatibility, browser UI or two-host/provider execution.
Those belong to #588–594, with independent QA. Historical CLI 0.146.0 is only
repository provenance; neither it nor the host's reported 0.154.0 is certified by
these data-only protocol fixtures.
