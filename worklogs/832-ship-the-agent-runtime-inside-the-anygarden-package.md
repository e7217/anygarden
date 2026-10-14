# refactor(agent): ship the agent runtime inside the anygarden package (#832)

- Commit: `02263fd` (02263fdb072497aa258544ee011ad4aabeaa38e2)
- Author: Changyong Um
- Date: 2026-10-14T16:01:21+09:00
- PR: #832

## Situation

The agent runtime lived in a separate distribution (`anygarden-agent`) and the machine daemon resolved it at spawn time either from PATH, from a sibling-binary beside the daemon's interpreter, or — as last resort — via `uvx anygarden-agent` from PyPI. Remote machines are told to install only `anygarden[machine]`, so the agent they actually ran came from PyPI rather than from the same release that was running on the server. This caused version drift between the server and its agents (#683) and forced every release to ship two packages (the cluster package plus the standalone agent package). The machine integration in #754 had already folded the `anygarden-machine` distribution into this same package; the agent was the last remaining split.

## Task

- Move the full agent runtime (`packages/agent/anygarden_agent/`) into the main cluster package as `packages/cluster/anygarden/agent/`, updating every import across source and tests.
- Move agent tests, docs, example profiles, and changelog under `packages/cluster/`.
- Expose the runtime's pip dependencies as a new `agent` extra in `packages/cluster/pyproject.toml`; make the existing `machine` extra depend on `anygarden[agent]`.
- Change the spawner to launch agents as `sys.executable -m anygarden.agent.cli` so version always matches the running daemon — no PATH lookup, no uvx fallback.
- Drop the `anygarden-agent` / `anygarden-client` console-script entry points; route through `anygarden agent` / `anygarden client`.
- Raise `requires-python` to `>=3.12` (agents used 3.12 from uvx; the cancel path drops an API that was removed in 3.12 on 3.11).
- Remove the agent CI step, `anygarden-agent` release tag, and `release-agent` Makefile target; update README, CONTRIBUTING, and runbooks.

## Action

- **`packages/cluster/anygarden/agent/`** — all modules moved from `packages/agent/anygarden_agent/`; internal imports rewritten from `anygarden_agent.*` to `anygarden.agent.*` (e.g. `cli.py`, `client.py`, `secrets.py`, `integrations/base.py`, `integrations/codex_cli.py`, `integrations/room_execution.py`, `runtime/execution/failure_feedback.py`, `runtime/execution/launch.py`, `runtime/execution/room.py`).
- **`packages/cluster/anygarden/machine/spawner.py:1035–1065`** — replaced the three-branch binary resolution block (sibling, `shutil.which`, uvx) with a single `[sys.executable, "-m", "anygarden.agent.cli", ...]` invocation; updated three in-comment references from `anygarden_agent.*` to `anygarden.agent.*`.
- **`packages/cluster/pyproject.toml`** — bumped `requires-python` to `>=3.12`; rewrote `[agent]` extra to inline the runtime's direct deps (websockets, httpx, pydantic, click, structlog, pyyaml, psutil); added `anygarden[agent]` to the `[machine]` extra; removed the `[tool.uv.sources]` stanza that pointed `anygarden-agent` at the workspace sibling.
- **`packages/cluster/tests/agent/`** — all agent tests moved from `packages/agent/tests/`; imports updated; `test_cli.py` and `test_client.py` updated to use the new module paths.
- **`packages/cluster/tests/machine/test_spawner.py`** — removed all `patch("anygarden.machine.spawner.shutil.which", return_value=...)` patches (no longer needed); dropped `test_spawn_falls_back_to_uvx` test (the uvx path no longer exists).
- **`packages/cluster/docs/agent/`** — CHANGELOG, README, architecture, engines, local-execution docs, and example profiles moved from `packages/agent/`.
- **`packages/cluster/docs/machine/architecture.md`, `operations.md`** — import references updated to `anygarden.agent.*`.
- **`.github/workflows/ci.yml`, `release.yml`** — removed the separate agent CI job and `anygarden-agent` release-tag step.
- **`Makefile`** — removed `release-agent` target.
- **`README.md`, `CONTRIBUTING.md`, `docs/runbook/`** — install and binary-path references updated.
- **`uv.lock`** — workspace member `anygarden-agent` removed; lock updated.
- **`pyproject.toml`** (root) — removed `packages/agent` from workspace members.
- **`packages/agent/`** — directory fully deleted (pyproject, CI workflow, gitignore, LICENSE, tests/conftest).

## Decisions

**Option A — keep `anygarden-agent` as a separate pip package but publish it from the same source tree (monorepo sibling)**
Rejected: still requires two separate wheel uploads per release and does not eliminate PyPI drift for machines that install `anygarden[machine]` without pinning the agent version.

**Option B — embed the runtime inside `anygarden` and launch via `python -m anygarden.agent.cli`** *(chosen)*
The daemon's own interpreter always holds the version that was installed, so version drift is structurally impossible. No PATH resolution heuristics, no uvx network round-trip on cold nodes. The approach mirrors how `anygarden-machine` was folded in via #754.

**Option C — keep uvx but pin the version hash**
Rejected: still requires a second release artifact and adds a network dependency at spawn time; pin management adds toil.

**`requires-python` bump to 3.12** — the agent runtime's cancel-path was already 3.12-only (uses an asyncio API deprecated/removed on 3.11); previously uvx fetched a 3.12 interpreter transparently. With the daemon running the agent under its own interpreter, the whole distribution must guarantee 3.12.

**Assumption**: if a remote machine ever has a legitimate reason to pin a different agent version than the server (e.g., staged rollouts), this decision should be revisited — the old uvx path gave that flexibility at the cost of correctness.

## Result

- Agents now run in the same venv and at the same version as the server daemon; the "which binary actually ran?" forensic log still records the interpreter path.
- The `packages/agent` workspace member is gone; CI has one fewer job; the release pipeline ships one package.
- `anygarden[machine]` installs the full agent runtime as a transitive dependency, so no manual agent installation step is needed on remote machines.
- 157 files changed: 498 insertions, 991 deletions (net −493 lines).
- A test (`test_cli.py`) runs `python -m anygarden.agent.cli --help` for real, so a broken entry point fails in CI rather than on a remote host.
