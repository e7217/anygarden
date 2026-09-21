# feat(machine): persist agent stdout/stderr to a per-agent log file

- Commit: `cfdd7ad`
- Author: Changyong Um
- Date: 2026-09-21
- PR: —

## Situation

`watch_process` drains the agent's pipes (#638) but discards stdout, and before that nothing read them at all. Either way an agent's own logs never reached disk or the node log. On 2026-09-19 every local agent sat idle with no visible reason; the only way to see why was to stop one agent and re-run its exact command in the foreground, which finally showed the single `ws.disconnected` warning that explained the outage. That is an expensive way to read one log line.

## Task

- Make an agent's stdio readable after the fact, across respawns.
- Bound the file so a chatty agent cannot fill the disk.
- Keep logging strictly best-effort: it must never disturb spawning, supervision, or the crash report.

## Action

- `packages/machine/anygarden_machine/supervisor.py`:
  - `_AgentLog` — append-only sink over one path, tracking size and rotating to `<name>.1` at `AGENT_LOG_MAX_BYTES` (5MB, one generation kept). Any `OSError` logs a warning and disables the sink.
  - `_open_agent_log()` returns `None` when the path cannot be opened, so a bad path degrades to the previous behavior.
  - `_drain` takes a `sink` and mirrors every chunk into it; `watch_process` takes `log_path`, wires the sink into both drains, and closes it in `finally`. The stderr tail for crash reporting is unchanged.
- `packages/machine/anygarden_machine/spawner.py`: passes `log_path=agent_root / "agent.log"` when the agent has a materialized directory, `None` otherwise.
- `packages/machine/tests/test_supervisor.py`: stdout and stderr both land in the file; rotation caps the live file and leaves exactly one backup (cap monkeypatched to 16KB); an unwritable path still reports a normal exit.
- `packages/machine/tests/test_spawner.py`: the spawner points the watcher at `<agent_dirs_root>/<agent_id>/agent.log`.

## Decisions

- Options weighed: a per-agent file (chosen), forwarding every line into the node's structlog, or leaving stdout discarded.
- Forwarding to the node log was rejected because agent output is high-volume and per-agent; interleaving six agents into a log that already carries SQL echo makes both harder to read. A file per agent keeps each agent's story in one place and is greppable.
- The file lives in the agent root because the machine materializer preserves agent-created files there, so logs survive a respawn — the same property `engine_session_store` relies on.
- Rotation is deliberately minimal (one backup, no compression, no time-based policy). It only has to bound disk use; the node is not a log aggregator.
- Size is tracked in memory rather than by `stat()` per write, and writes are plain buffered file writes rather than `to_thread` hops. Chunks are at most 64KiB and the node already logs synchronously.
- Note: the agent's cwd is the agent root, so an agent can read (or overwrite) its own `agent.log`. Acceptable for a debug artifact; if that ever matters, the file should move outside the agent dir.

## Result

- `cd packages/machine && uv run pytest`: 523 passed, 2 skipped (4 new).
- `cd packages/cluster && uv run pytest`: 1770 passed, 1 deselected — the integrated node's `LocalDaemon` uses the same spawner.
- Lint on the changed files reports only pre-existing findings.
- Not yet observed on a live agent; after merge, `~/.anygarden/agents/<id>/agent.log` should start filling.
