# Room execution upgrade

Normal `codex-cli` and `pi-cli` room turns now run through
`LocalExecutionManager`, the process supervision and receipt implementation also
used by federation. CLI registration binds the private launch selection before
submission. Existing room wake policy, delegation, room queries, ambient context,
identity/memory/roster prompts, typing and lifecycle handling remain in place.

## Configuration and permissions

The room registration factory selects local room runtime classes. This option is
not a federation payload field. Federation still constructs ordinary Invocation
and isolated runtimes, rejects trusted permissions and external workspace writes,
and suppresses user configuration/skills as before.

Local Codex keeps the effective `CODEX_HOME` staged by the machine, or its existing
host-home fallback. OAuth, config.toml MCP definitions, skills and rules continue
to be read by the installed CLI. No auth/config files are rewritten. Permission
tiers retain the existing native sandbox and approval mapping, including trusted.
A selected native provider is passed as a structured config override. An omitted
Codex model retains the prior adapter default.

Local Pi keeps settings/auth/models under the agent root `.pi/agent` and sessions
under the agent root `sessions`; installation assets are resolved by Pi itself.
Local skills, context files, prompt templates and extensions remain enabled.
Restricted Pi turns select the read/grep/find/ls tool set. Pi does not provide an
OS sandbox; this is not an external-workspace isolation guarantee. The common
capability report continues to advertise no workspace enforcement.

Provider credentials enter through private stdin and are merged into only the
engine child's environment. The parent environment is not mutated. The server
transport token and private endpoint CONFIG/KEY are removed; the separately
staged self-MCP credential remains available. Endpoint key selection, Pi auth
conflict detection and managed-model file preservation still apply in room mode.

## Sessions and upgrade behavior

New receipts and native handles live under `.anygarden-execution/<engine>` inside
the agent root. Session identity includes agent, authority, room, thread,
workspace, permission tier, launch generation, provider/model and endpoint
revision. A process restart with unchanged selection resumes its stored handle.
Changing one of these boundaries starts a different session. A failed or unknown
turn is never automatically retried in a fresh native session.

Old Codex `.anygarden-engine-sessions.json` maps rooms to handles without recording
provider/model. At most once per room, the upgrade can import a handle when all
of the following are true:

- This is a root-room turn with no direct endpoint.
- Exactly one bounded native session file in the effective Codex home matches its
  UUID; its session metadata confirms that ID, workspace and selected provider.
- Its last turn-context model matches the current effective model.

If metadata is absent, ambiguous, too large to inspect, or incompatible, original
files are preserved and a new session starts. Starting directly with an endpoint
also consumes the old room import opportunity without importing it: later
turning that endpoint off cannot resurrect the old session. Native sessions
already migrated into the scoped manager store do not require another import.
Thus legacy continuation is guaranteed only for metadata-confirmed compatibility.

## Usage and cancellation

Each runtime run owns its measured usage accumulator. Pi sums measured assistant
message usage; invalid/missing token values do not become a measured zero.
Received tokens survive provider errors, abnormal exits, timeout and cancellation,
including process-tree cleanup. The room supervisor emits those measurements on
its existing terminal lifecycle frame. Existing neutral usage-ledger and budget
consumers remain unchanged; this does not add ledger idempotency.

## Validation limits

The room regressions call actual CLI registration, wake policy, supervisor,
manager, subprocess collectors and lifecycle handling, using fake executables.
Direct endpoint tests make HTTP requests only to loopback responders. They cover
Responses for Codex and Pi, and Chat Completions for Pi. Installed CLI version
checks are separate evidence; fake CLI HTTP tests do not demonstrate an installed
CLI completing a model turn. No real provider, deployment, production database
write or merge is part of this change.

## Supported CLI versions

Each adapter runs `<cli> --version` before every turn and accepts only exact,
verified versions (no prefix matching — `0.85.10` is not `0.85.1`):

| Engine | Supported versions | Package |
|---|---|---|
| `pi-cli` | `0.85.1` | `@earendil-works/pi-coding-agent` |
| `codex-cli` | `0.154.0`, `0.155.1` | `@openai/codex` |

The source of truth is `SUPPORTED_VERSIONS` in
`anygarden_agent/runtime/execution/{pi,codex}.py`; the server catalog mirrors it
(`GET /api/v1/agents/engines/{engine}/models` → `supported_versions`) and a test
keeps the two in sync.

When the installed CLI is outside this list, the turn fails before any model
request with `UNSUPPORTED_RUNTIME`. The activity log names both versions, e.g.
`UNSUPPORTED_RUNTIME: pi-cli 0.87.1 is not supported; this build requires 0.85.1`,
and the *Create Agent on Machine* dialog warns when the machine's detected version
does not match. For Codex, the machine's *Update engine* action installs `@latest`,
which can move the CLI off the supported version. Pin it with:

```bash
npm install -g @openai/codex@0.155.1
```

### Managed Pi install (#688)

Pi agents no longer use the `pi` that happens to be first on `PATH`. The machine
daemon owns a private, version-pinned install:

```
~/.anygarden/engines/pi-cli/<pinned version>/node_modules/.bin/pi
```

- **Provisioning.** At daemon start, if the managed install is missing and the
  machine already has a global `pi` (or `ANYGARDEN_MANAGED_PI=1`), the daemon
  runs `npm install --prefix <that dir> @earendil-works/pi-coding-agent@<pinned>`.
  A failure is only logged; the daemon still starts. `ANYGARDEN_MANAGED_PI=0`
  disables this.
- **Update engine** for Pi (re)installs the pinned version into the managed
  prefix. It never installs `@latest` globally, and it doesn't touch the
  operator's own `pi`.
- **Detection** advertises `pi-cli` from the managed install. A global `pi` is
  considered only with `ANYGARDEN_PI_USE_PATH=1` in the daemon environment.
- **Agents** receive the managed path as `ANYGARDEN_PI_EXECUTABLE`. Without it,
  and without `ANYGARDEN_PI_USE_PATH=1`, a Pi agent refuses to start with
  "managed Pi install is missing". There is no silent `PATH` fallback. Setting
  `ANYGARDEN_PI_EXECUTABLE` in the daemon environment overrides the managed path.
- **Air-gapped nodes.** Pre-seed the prefix, for example
  `npm install --prefix ~/.anygarden/engines/pi-cli/0.85.1 <local tarball or registry package>`,
  or point `ANYGARDEN_MANAGED_ENGINES_DIR` at a pre-populated root.

Operators can upgrade their own global `pi` freely. Moving agents to a new Pi
release is a code change: bump `PI_PINNED_VERSION` (machine) and `ENGINE_VERSION`
(agent) together, which a test enforces. The new version installs into its own
directory next to the old one.

Supporting a new CLI release is a code change: verify the JSON event stream,
isolation flags and config schema against the adapter, then add the version to
`SUPPORTED_VERSIONS` (and the catalog entry).
