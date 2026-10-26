# chore(release): bump anygarden to 0.25.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-26
- PR: — (release)

## Situation

`anygarden 0.24.0` was tagged on 2026-10-26 at `850ec61`. Since then `main`
gained:

- #935 (#930) metric-driven campaigns that cycle until their goal is met
- #936 (#930) the missing #930 worklogs

Schema migrations in this range: 097 campaigns, 098 instruction revision
context, 099 campaign eval evaluator digest.

## Task

Cut a release that carries campaigns and their three migrations. 0.24.0
nodes can take this release through the in-place web update (#927).

## Action

- `packages/cluster/pyproject.toml`: `0.24.0` → `0.25.0` (minor: new feature).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.25.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
