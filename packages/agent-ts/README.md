# @anygarden/agent-ts

Anygarden TypeScript agent runtime. Parallels the Python `anygarden_agent` package
for engines whose official SDK ships in TypeScript:

- **Claude Code** (`@anthropic-ai/claude-agent-sdk`) — MVP target
- Codex TS SDK — Phase 2
- Gemini CLI SDK — Phase 2

Issue: [#73](https://github.com/e7217/anygarden/issues/73)

## Status

Spike / work-in-progress. The Python runtime remains authoritative for
API-style engines (Anthropic/OpenAI) and framework engines (OpenHands,
Deep Agents). Select `runtime: "typescript"` on an Agent row to spawn
the TS runtime instead.

## Preview dependency

This package pins `@anthropic-ai/claude-agent-sdk@0.2.110` and consumes
the SDK's **V2 preview (`unstable_v2_*`)** surface:

- `unstable_v2_createSession` — persistent multi-turn session
- `unstable_v2_resumeSession` — resume by id
- `SDKSession.stream()` — async generator of `SDKMessage`

The V2 API is marked `@alpha` by Anthropic. Upgrading the SDK requires a
manual smoke test; we expect signature drift.

## Development

From the monorepo root:

```bash
# Install dependencies (npm workspaces)
npm install

# Run the TS agent test suite
npm run test:ts

# Build the CLI bundle
npm run build:ts

# Lint
npm run lint:ts
```

Or directly inside this package:

```bash
cd packages/agent-ts
npm test
npm run build
npm run lint
```

## CLI

```bash
anygarden-agent-ts --engine claude_code --name my-agent --server ws://localhost:8000
# env: ANYGARDEN_TOKEN=<jwt>
```

The CLI reads `ANYGARDEN_TOKEN` from the environment and never accepts the
token on argv — mirrors the Python runtime contract.

## Local smoke test (same room, two runtimes)

1. `make dev` to start the cluster + frontend.
2. Create an agent via admin UI with `engine=claude_code` and
   `runtime=python`; wait for it to come online.
3. Create a second agent with `engine=claude_code` and
   `runtime=typescript`; the machine daemon spawns the TS runtime.
4. Send a message in their shared room; both should respond via their
   respective runtimes without interfering with each other.

If the TS runtime is not installed globally, the machine daemon will
fall back to `npx -y @anygarden/agent-ts`. Check the `agent_binary_resolved`
structured log line for which path was picked.
