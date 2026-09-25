# Local Codex execution boundary (#589)

`anygarden_agent.runtime.execution` runs a Codex invocation without importing
ChatClient, joining a room WebSocket, claiming a task, or deciding a retry.
It implements the #587 contract at PR596 `ac7667718f26a78e4a4b686011ee456ab074c9b5`
(the execution/result contract is unchanged from `7ff28be`).

The backend requires **POSIX**. It records the Codex CLI version for session
separation but attempts execution with any installed version. Command or event
stream incompatibility fails through the normal engine result path. This is
local protocol/process compatibility, not evidence of provider authentication,
model access, live AI success, or two-node acceptance.

## Node integration

```python
from anygarden_agent.runtime.execution import (
    CodexRuntime, Invocation, LocalExecutionManager, SessionScope,
)

manager = LocalExecutionManager(
    node_data_dir / "executions",       # local receipts; outside agent workspaces
    CodexRuntime(codex_executable),      # explicit absolute executable Path
    authorize=current_local_permission, # SessionScope -> bool; required, no default allow
)
scope = SessionScope(
    execution_node_id=node_id, agent_id=agent_id,
    authority_node_id=authority_id, channel_id=channel_id,
    thread_root_id=thread_root_id, workspace_binding_id=binding_id,
    workspace_epoch=workspace_epoch, policy_epoch=policy_epoch,
)
receipt = await manager.start(Invocation(
    execution_id=durable_execution_id, scope=scope,
    prompt=selected_task_input, instructions=local_instructions,
    workspace=managed_workspace, runtime_home=materialized_agent_codex_home,
    environment=explicit_runtime_environment, model=selected_model,
))
async for event in manager.events(receipt.execution_id, after=committed_cursor):
    # Apply (execution_id, event.sequence) once in the consuming transaction.
    await consume_local_event(event)
receipt = await manager.reconcile(receipt.execution_id)
# cancel() acknowledges the request; read terminal receipt for stop evidence.
await manager.cancel(receipt.execution_id)
await manager.close()
```

The node owns policy, per-agent materialization, selected input, generation/lease
fences, mapping delegation IDs to durable execution IDs, and the transaction that
publishes a result. The manager never bypasses those responsibilities. Existing
legacy wrappers remain available; node task dispatch/result publication must be
connected by #588/#592 integration. This PR alone does not replace all room WS
traffic or prove authority publication is at most once.

The constructor's directory and executable, invocation's paths/environment and
`authorize` callback are **trusted local inputs**, not federation payload fields.
No server environment is inherited. `HOME` and `CODEX_HOME` are assigned to the
explicit agent runtime home; credentials must be deliberately materialized there
or explicitly supplied to this invocation. Node credential variable names are
rejected. User config/rules are disabled; instructions are supplied as turn input.
Project/runtime-owned behavior still needs the existing local materializer and
policy checks. The manager is not an OS credential or filesystem sandbox.

## Receipts, sessions, and cancellation

- One process-lifetime OS lock owns the receipt directory. SQLite transactions
  atomically store state and ordered events with `synchronous=FULL`. A duplicate
  ID with identical canonical content returns the original receipt; changed
  input is a conflict. Current authorization is checked before dedup/results,
  at queue entry, after version probing, and before prompt delivery.
- Sessions are keyed by node, **agent**, authority, channel, thread, workspace
  binding and epoch, policy epoch, engine and version. Queues serialize each
  scope, including across manager restart through persisted native handles.
  Changing materialization without a new epoch is refused. Legacy room-only
  session files remain untouched and are never imported into this namespace.
- `launching` is committed before spawning. On recovery, unfinished launch/run
  receipts become `unknown`; queued work is cancelled as `not_started`. Neither
  path automatically relaunches. An unknown result blocks further work in that
  session scope until a local operator reconciles side effects and explicitly
  accepts a new policy scope. Do not delete receipts to recover an invocation.
- `cancel()` records `cancel_requested` first. Terminal `cancelled` requires
  `not_started` or verified `stopped`; `failed` carries a closed `error_code`
  (`ENGINE_ERROR`, `TIMEOUT_STOPPED`, `UNSUPPORTED_RUNTIME`, `POLICY_DENIED`). A
  timeout without confirmed termination is `unknown`. Successful results use
  `process_state=finished`. Native nonzero without definite failure evidence is
  `unknown`, including resumed runs that may already have tool effects.
- On policy revocation, the trusted node owner calls `await manager.revoke(scope)`
  after updating its authorization source. This invalidates the session and
  requests cancellation even after access is denied. A new grant must use a new
  policy epoch. Cancellation receipt is not a claim that tool effects are undone.
- POSIX termination covers the owned process group and observed descendants,
  including ordinary tool children and observed detached children. It is not a
  hostile-process containment system (cgroups/containers are not added here).
  Unverifiable termination returns unknown. Windows execution, external workspace
  enforcement and external workspace writes are unsupported and fail explicitly.
- Events contain bounded final text and selected progress metadata. Native
  handles, commands, raw stderr and exception strings are not published in events.
  Receipts are local data and must not be forwarded wholesale to a peer.

The legacy Codex integration also no longer retries a nonzero resume as a fresh
turn, and nonzero failures do not advertise transient retry eligibility.

## Reproduce provider-free validation

Run the agent tests using the matching workspace packages (not older installed
editable copies):

```sh
PYTHONPATH=packages/agent:packages/cluster:packages/machine \
  python -m pytest -q packages/agent/tests -p no:libtmux
```

The product tests use real fake subprocesses for progress/result, partial side
effects, FIFO, scope isolation, cancellation during spawn, timeout with a tool
child, replay conflicts, revocation and cold receipt recovery.

The opt-in actual CLI probe requires Linux network namespace permission, `ip`,
and the exact CLI binary. It checks that only loopback exists before launching
anything; uses an isolated temporary home, an inert fixture key and a local SSE
response server; and recreates the manager between fresh and resumed turns.
It is intentionally not part of CI's automatic suite or a live provider test.

```sh
unshare -n sh -c 'ip link set lo up; PYTHONPATH=packages/agent exec python \
  packages/agent/tests/test_execution/fixtures/offline_codex_probe.py \
  /absolute/path/to/codex'
```

Observed on 2026-09-16: installed 0.154.0, fresh and resumed invocation succeeded,
exactly two loopback requests, no external network interface. The probe explicitly
configures a fixture provider; it does not rely on `OPENAI_BASE_URL`, which did
not redirect this version's default WebSocket endpoint in the isolated probe.

The native JSONL event and explicit resume interface follow the
[official OpenAI non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).
CLI `--version`, `exec --help` and `exec resume --help` were also inspected locally.
