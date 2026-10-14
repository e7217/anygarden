# refactor(agent): ship the agent runtime inside the anygarden package (#832)

- Commit: `02263fd` (02263fdb072497aa258544ee011ad4aabeaa38e2)
- Author: Changyong Um
- Date: 2026-10-14T16:01:21+09:00
- PR: —

## Situation

#754 folded `anygarden-machine` into `anygarden.machine` and left "merge `anygarden-agent` the same way" as a follow-up that was never filed. The `anygarden[agent]` extra still pulled the separate `anygarden-agent>=0.8` distribution from PyPI, and the machine spawner looked for an `anygarden-agent` binary on PATH and fell back to `uvx anygarden-agent`. The admin UI tells remote hosts to install only `anygarden[machine]`, so those agents always went through uvx and ran whatever PyPI had, not the build matching the server (#683). Releases had to ship twice too: `anygarden` 0.19.0 and 31 agent commits after `anygarden-agent` 0.13.0 were still unreleased.

## Task

- One distribution: move the runtime to `anygarden.agent` and its tests into the cluster suite
- A machine install has to be able to run agents without uvx
- The daemon has to run the agent version that it ships with
- Retire the `anygarden-agent` / `anygarden-client` scripts, CI step, release tag and Makefile target
- Update current-tense docs. Historical design docs, plans, research and migration comments stay as they are

## Action

- `git mv packages/agent/anygarden_agent → packages/cluster/anygarden/agent`, tests → `packages/cluster/tests/agent` (added `test_execution/__init__.py`; dropped the agent `conftest.py` whose `req` hook duplicated the cluster one). README, docs, example profiles and CHANGELOG → `packages/cluster/docs/agent/`. Removed `packages/agent/{pyproject.toml,LICENSE,.gitignore,.github/}`
- `anygarden_agent` → `anygarden.agent` across code, tests and `patch()` targets. Migrations 016/049/080 stay untouched
- `packages/cluster/pyproject.toml`: the `agent` extra lists the runtime's dependencies; `machine` includes `anygarden[agent]`; workspace source removed; `requires-python = ">=3.12"`. Root `pyproject.toml` and `uv.lock` updated to match
- `anygarden/machine/spawner.py`: replaced the sibling/PATH/uvx resolution with `[sys.executable, "-m", "anygarden.agent.cli", ...]`. `agent_binary_resolved` now logs `source="interpreter"` and `path=sys.executable`
- `tests/machine/test_spawner.py`: four uvx/PATH/sibling tests replaced by a parametrized interpreter test and `test_spawned_agent_command_actually_runs`, which executes the built prefix with `--help`. Removed 24 dead `shutil.which` patches; `test_endpoint_transport.py` slices args after the 3-element prefix
- `tests/test_arch_room_ws_e2e.py` and `test_room_usage_bridge.py` import `tests.agent.test_room_execution` instead of loading it by file path; `test_cli.py` and `test_cli_usability.py` read the moved README
- `anygarden/agent/cli.py`: click names `agent` / `client`. `anygarden/cli.py` checks `anygarden.agent.cli` for integrated mode
- CI: removed the agent test step. Release: removed the `anygarden-agent-v*` trigger and mapping. Makefile: removed `release-agent` and the agent `req-report` line. Also updated `scripts/test.ps1` and the `.githooks/post-merge` comment
- Docs: README packages table (extras plus an uninstall note for retired distributions), CONTRIBUTING, cluster README, machine architecture/operations, the federation-two-node, room-execution-upgrade and retired-engines runbooks, and the EngineGlyph comment

## Decisions

- **How the daemon launches agents**: chose the daemon's own interpreter over PATH lookup (version can drift through shadowing) and over keeping uvx (a silently different build, which is what #683 reported). The deciding point was that after the merge the runtime sits in the daemon's own wheel.
- **Entry point**: the plan said `python -m anygarden agent`, but `anygarden/__main__.py` runs `cli:main`, which has no `agent` command. A clean-venv check caught this, not the mocked tests. Switched to `python -m anygarden.agent.cli`, which matches the existing `python -m anygarden.machine.cli run` pattern, and added a test that runs the real command.
- **`machine` ⊇ `agent`**: without uvx, a `[machine]`-only host could not start agents at all. The dependency sets already overlapped completely, so install size is unchanged.
- **Python 3.12 floor**: the plan kept `>=3.11`, but on 3.11 two cancel-path usage tests in `test_room_execution` fail (`input_tokens` is None). Agents used to get a 3.12 interpreter from uvx because `anygarden-agent` required it. They now run on the daemon's interpreter, so the whole distribution needs 3.12 to keep that guarantee.
- **Tests join the cluster suite** (as with `tests/machine/`) rather than keeping a second testpath, CI step and JUnit file.
- Revisit if `python -m anygarden` is ever pointed at the unified dispatcher, or if 3.11 support is needed again (fix the cancel-path usage first).

## Result

- Full cluster suite with the agent tests included: 3691 passed plus 1 failure afterwards fixed (README path); targeted re-run 1376 passed. The agent suite under xdist: 751 passed, 2 skipped (no Node available)
- Wheel built from source contains `anygarden/agent/**` including `pi_self_tools.{json,mjs}`. In a clean 3.11 `[machine]` venv and a 3.12 `[agent]` venv, `python -m anygarden.agent.cli --help` works and FastAPI/SQLAlchemy are not installed
- Lint: no new findings in changed files beyond the pre-existing baseline
- Pending: release `anygarden` 0.19.0 (blocked on the Engine Smoke OpenAI key secret). PyPI `anygarden-agent` is left as is. Existing venvs should `pip uninstall anygarden-agent`
