# Endpoint launch transport integration

This stack extends PR676 with the production `Invocation.endpoint` field and the local launch transport. It does not install the common room turn adapter; that adapter remains #653 work.

The server includes provider/model and private endpoint CONFIG/KEY in its desired-state frame. The machine preserves the nonsecret provider and endpoint-configured marker, forwards provider/model as CLI arguments, and pipes configuration/keys to agent stdin. Missing or malformed Pi providers are refused before process creation. Provider/direct-endpoint configuration currently requires the Python agent runtime.

The agent entry point validates the original JSON types for CONFIG/KEY before the legacy secrets loader can coerce them. Nonstring values are rejected without echoing the value. Absent KEY is supported only by an explicitly keyless descriptor. The parser never reads endpoint credentials from ambient environment variables.

The entry point stores an `ExecutionLaunch` on `client.execution_launch`. A common room bridge must:

1. Build the normal local `Invocation` with its existing authorization, workspace, timeout and permission policy.
2. Call `client.execution_launch.bind(invocation)` exactly once before passing the invocation to `LocalExecutionManager`. Never reconstruct launch settings from a room or federation payload.
3. Set `client.execution_launch_ready = True` only after installing the common execution handlers. If a direct endpoint is configured and this marker is absent after engine setup, the CLI closes the client and refuses legacy/default-provider execution before joining rooms.

Binding applies the selected provider/model, descriptor and child-only key environment. It combines the original policy epoch, server generation and nonsecret provider/model/descriptor/ref/revision into a stable session scope. Including descriptor/revision also fences the interval between endpoint database commit and the later lifecycle generation bump. Identical inputs reuse the scope; endpoint or credential revision changes cannot reuse the previous native session even at the same generation. The production invocation fingerprint includes the descriptor through its dataclass serialization.

The local cross-component regression exercises database configuration, lifecycle frame construction, daemon token handling, the actual spawner's argv/stdin construction, the actual Click entry point and production Invocation binding. Process creation and the room runner are replaced with fixtures; the test awaits all daemon spawn tasks while those replacements remain active. Fake CLI/loopback HTTP tests use the production Invocation type. Neither test proves installed CLI compatibility or full room-turn execution.
