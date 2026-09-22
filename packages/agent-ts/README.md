# @anygarden/agent-ts

TypeScript transport client for Anygarden rooms. The ChatClient, protocol, routing, and coordination helpers remain available.

Engine execution uses the Python agent runtime with `codex-cli` or `pi-cli`. The Claude Code adapter and SDK have been removed. The TypeScript CLI refuses retired engines and Codex/Pi execution with migration guidance.

Existing agent settings and history are preserved. For a retired engine, create a Python Codex or Pi agent and explicitly transfer the settings you need. Existing Codex/Pi agents configured with TypeScript can be explicitly changed to `runtime=python`; no automatic conversion occurs.

## Development

From the repository root:

```bash
npm ci
npm run test -w @anygarden/agent-ts
npm run typecheck -w @anygarden/agent-ts
npm run build -w @anygarden/agent-ts
```
