# ci(cluster): run cluster tests in parallel with pytest-xdist (#753)

- Commit: `c4eace1` (c4eace1e260719c3efb7ffb5bf4cf17ad8be7770)
- Author: Changyong Um
- Date: 2026-09-30
- PR: #753 (issue; PR number assigned on creation)

## Situation

After #750 removed the fsync cost of file-backed test DBs, the Linux CI `Test cluster` step still took ~634 s (666 s before #750). The time was spread evenly over ~2,000 tests (~0.33 s each) that ran one after another on a single core, while GitHub's standard Linux runner has 4 cores.

## Task

- Run the cluster suite on all runner cores in CI.
- Keep the local default sequential so output stays readable while debugging.
- Make sure parallel runs do not add new flaky failures.

## Action

- `packages/cluster/pyproject.toml`: added `pytest-xdist>=3.5` to the `dev` extra; `uv.lock` updated (pytest-xdist 3.8.0, execnet 2.1.2).
- `.github/workflows/ci.yml`: the `Test cluster` step now runs `uv run pytest -x -n logical`. The first push used `-n auto`, but on CI that started only 2 workers: with psutil installed, `auto` counts physical cores, and the 4-vCPU runner reports 2. Follow-up commit `fa7ada0` switched to `logical`.
- `CONTRIBUTING.md`: under "Checks before you push", documented `cd packages/cluster && uv run pytest -n auto`.

## Decisions

- Options weighed:
  - `pytest-xdist` with `-n auto` in CI only — chosen.
  - Put `-n auto` in `addopts` so every run is parallel — rejected: interleaved output and harder debugging for a single failing test locally.
  - Split machine/agent/cluster into separate CI jobs — smaller gain (machine and agent take ~40 s together); can still be done later.
- Tests already use their own DB and `tmp_path`, so workers do not share state. Local runs: `-n 4` passed 6/6 times (~1m50s each); `-n auto` (8 workers) failed 2 of 4 runs with one failure each, always in `test_ws_handler.py::TestAgentCausalLink`.
- Those failures are #726, not new ones: the same tests also fail sequentially, and CI's #726 failures are in the same class. The in-memory test engine uses `StaticPool`; when its single connection is closed mid-test (e.g. a cancelled WS handler task), the pool opens a new, empty in-memory DB, which shows up as `no such table`, FK failures or `no active connection`. Higher CPU load makes that timing more likely.
- Assumption: CI runners keep 4 vCPUs, so `-n logical` means 4 workers there. On a bigger runner, #726 may show up more often until it is fixed.

## Result

- Local cluster suite: 6m21s sequential → ~1m50s with `-n 4`, 2034 passed.
- CI `Test cluster` step: ~634 s → 373 s with `-n auto` (2 workers). The `-n logical` run is measured on the PR.
- #726 is still open; its root cause and the proposed fix (file-backed DB for the WS fixtures) are recorded on the issue.
