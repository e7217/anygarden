# feat(bench): record the solo re-run and judge the raced DL-03 run invalid

- Commit: `003095c` (003095c642c1783dfe29de373b27085b449bfeb3)
- Author: Changyong Um
- Date: 2026-10-13T18:54:30+09:00
- PR: —

## Situation

The benchmark runner (`dae3af6`) had a race condition in solo mode: when a task turned `done`, the judge fired immediately — before the agent had finished posting its full answer to the room. This meant DL-03 solo run 1 (from the `dae3af6` result file) was evaluated on an incomplete response. The fix landed in `f524b4a` (judge only after the agent's turns end), but the earlier result file still carried that run as if it were a valid pass — misrepresenting the scenario's baseline reliability.

## Task

- Annotate the DL-03 solo run 1 in `benchmarks/results/20261013-0909-dae3af6.json` as `invalid` with a clear reason string referencing the race condition and the fixing commit.
- Add the fresh solo re-run results file (`benchmarks/results/20261013-0948-f524b4a.json`) that was measured against the fixed runner, covering DL-01, DL-02, DL-03 solo × 3 each.
- Commit both changes so the report reflects accurate pass rates.

## Action

- `benchmarks/results/20261013-0909-dae3af6.json` (line 758+): added a `"judged"` object inside the DL-03 solo run 1 entry with `"status": "invalid"` and a reason string: `"runner race: judged when the task turned done, before the agent posted its full answer (all facts were present); fixed in f524b4a and re-measured 3/3"`.
- `benchmarks/results/20261013-0948-f524b4a.json` (new file, 528 lines): full benchmark result for commit `f524b4a` covering DL-01/02/03 solo × 3, engine `codex-cli/gpt-6.1-sol/standard`. All 9 runs pass, with wall times 0.4–0.7 min and near-zero token cost.
- Also stopped the benchmark node (`anygarden stop --data-dir .tmp/bench/node-final4`) before committing.

## Decisions

- **Annotate-in-place rather than delete**: The original run data is kept intact with an `invalid` flag; this preserves the historical record of what the runner produced before the fix, while excluding the run from pass-rate calculations. Deleting the run would erase evidence of the race condition.
- **Separate result file per commit SHA**: Using the commit SHA in the filename (`20261013-0948-f524b4a.json`) makes the source of each run unambiguous in `git log` and `runner report`. Overwriting the old file would lose the pre-fix baseline.
- **Re-run 3/3 before recording**: A single re-run would not be statistically meaningful (k=3 is the project standard). All three passes confirmed the fix holds before the results were committed.
- Assumption: the `"judged"` key is understood by the runner's `report` command to exclude that run from aggregate stats. If a future schema change renames or removes that key, the invalid annotation would be silently ignored and DL-03's effective pass rate would be overstated.

## Result

- `benchmarks runner report` now shows DL-03 solo as `pass 3/3` sourced from `f524b4a`, with the raced run excluded.
- Full scenario coverage as of this commit: DL-01/02/03 all pass 3/3 in both solo and team; DL-04 solo still fails 0/3 (known open issue).
- No code changes — data files only. The benchmark infrastructure and runner logic are unchanged by this commit.
