# 345 — Collapse agent process cwd to agent root

## Situation

The machine spawner used `<agent_root>/workspace/` as the agent
process cwd and then added bridge files back to canonical materialized
state (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `memory/shared/`,
`memory/outbox/`). That protected codex's `workspace-write` sandbox,
but each new engine quirk added another bridge and stale-shadowing
case.

## Task

Make the Python agent process start from `<agent_root>` directly,
preserve existing runtime output, remove bridge requirements for
claude-code and gemini-cli, and keep managed instructions/config from
being mutated mid-session.

## Action

- Changed `Spawner.spawn()` to pass `cwd=<agent_root>` to
  `doorae-agent`.
- Reworked materialize pruning to refresh only materializer-managed
  top-level entries and preserve agent-created root output.
- Added legacy `workspace/` migration that moves non-managed runtime
  files upward when there is no conflict.
- Removed claude/gemini workspace bridges and changed adapter cwd
  assumptions from `Path.cwd().parent` to `Path.cwd()`.
- Added claude-code `permissions.deny` entries for managed files.
- Verified codex-cli 0.128.0 has `writable_roots` but no
  `read_only_paths`; kept a codex-only SDK `workspace/` fallback so
  codex `workspace-write` remains narrow without changing the machine
  subprocess cwd.

## Result

The default runtime model is now `agent_root` as cwd, with no
workspace bridge for claude-code or gemini-cli. Codex remains protected
by a narrow SDK workspace until Codex exposes managed-file read-only
exceptions. Targeted machine and agent tests cover materialization,
migration, spawn cwd, permission settings, and engine cwd assumptions.
