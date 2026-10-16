# fix(codex): let the workspace-write sandbox write the agent outbox (#858)

- Commit: `e28167e` (e28167e104d9311585269302f728c3ea4827810b)
- Author: Changyong Um
- Date: 2026-10-16T15:09:50+09:00
- PR: — (refs issue #858)

## Situation

Codex agents running under the `workspace-write` sandbox are only permitted to write files inside `<agent_root>/workspace`. The platform instructs agents to drop reply attachments into `workspace/memory/outbox`, which is a symlink pointing to `<agent_root>/memory/outbox` — a directory outside the allowed workspace root. The sandbox resolves the symlink and then rejects the write, so standard agents probably could not write there. The layout dates from #290; #854 made the outbox the path to reply attachments, so the gap now blocks attachments.

## Task

- Allow codex agents with `workspace-write` sandbox to write to the real outbox directory (`<agent_root>/memory/outbox`).
- The fix must work for both `codex exec` (fresh turns) and `codex exec resume` (continued sessions), since `--add-dir` is only accepted by `codex exec`.
- Must not break agents running under `read-only` or `danger-full-access` sandbox modes.

## Action

**`packages/cluster/anygarden/agent/integrations/codex_cli.py`**:
- Added `_outbox_writable_root_args(sandbox, outbox_dir) -> list[str]` (lines 119–131): returns `["-c", "sandbox_workspace_write.writable_roots=[\"<outbox_dir>\"]"]` when `sandbox == "workspace-write"` and `outbox_dir` is not `None`; otherwise returns `[]`.
- Updated `_resolve_codex_cli_args` (line 158) to splat `_outbox_writable_root_args(sandbox, outbox_dir)` into the returned CLI args list.
- Updated the legacy CLI adapter turn loop (lines 411–413) to pass `outbox_dir=Path.cwd() / "memory" / "outbox"` when building the codex command.

**`packages/cluster/anygarden/agent/runtime/execution/room.py`**:
- Added `outbox_dir: Path | None = None` field to `RoomCodexRuntime` (line 36).
- Overrode `command()` (lines 38–55) to import `_outbox_writable_root_args` and insert the writable-root args at the `-o` position, after the sandbox/approval flags are patched in.

**`packages/cluster/anygarden/agent/integrations/room_execution.py`**:
- Line 129: sets `runtime.outbox_dir = self._root / "memory" / "outbox"` when the engine is `codex-cli`, so `RoomCodexRuntime.command()` receives the real path.

**Tests**:
- `tests/agent/test_integrations/test_codex_cli.py` (line 72): asserts that `workspace-write` builds include the `writable_roots` flag with the expected outbox path; line 78 asserts `read-only` builds do not.
- `tests/agent/test_room_execution.py` (line 395): asserts the room runtime also injects the correct flag.

## Decisions

**Why `-c sandbox_workspace_write.writable_roots=[...]` instead of `--add-dir`?**
- `--add-dir` is only accepted by `codex exec`, not `codex exec resume` (#498 context). Using `--add-dir` would break session continuity for resumed turns.
- `-c` config flags are accepted uniformly by both `exec` and `exec resume` (verified 2026-10-16 with codex-cli 0.162.1: `exec resume --help` has no `--add-dir`, and both exec and resume accepted the `-c` form).
- Rejected: patching the symlink target — fragile, relies on filesystem layout staying stable.
- Rejected: rewriting the outbox path inside the workspace — breaks other consumers (outbox poller, attachment scanner) that already rely on the real `memory/outbox` location.
- Assumption: if codex-cli ever stops accepting `sandbox_workspace_write.writable_roots` via `-c`, this will need revisiting.

**Why a separate `_outbox_writable_root_args` helper instead of inlining?**
- Both the legacy CLI adapter and the room runtime (`RoomCodexRuntime`) need the same logic; extracting it avoids duplication and makes the guarding condition (`sandbox != "workspace-write"`) testable in isolation.

## Result

- Codex now gets the outbox as an extra writable root under `workspace-write`. Confirmed on a real room turn from this branch (isolated node, standard agent): the session's `sandbox_policy.writable_roots` contained `<agent_root>/memory/outbox`.
- **Not verified:** that the sandbox then actually allows the write. This VM (Ubuntu 24.04, `kernel.apparmor_restrict_unprivileged_userns=1`) cannot start bwrap at all, so standard codex agents still cannot write any file here. Issue #858 stays open for a check on a host with working user namespaces and for the AppArmor ops guidance.
- `read-only` and `danger-full-access` sandboxes are unaffected (guard returns `[]`).
- Session resume (`codex exec resume`) works correctly because `-c` is used throughout.
- Unit tests added covering both the positive (workspace-write + outbox present) and negative (read-only) paths for the CLI adapter and the room runtime.
- Cluster pytest: 3841 passed / 7 skipped.
