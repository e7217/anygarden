# chore(release): bump anygarden to 0.28.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-28
- PR: — (release)

## Situation

`anygarden 0.27.0` was tagged on 2026-10-28 at `da70404`. Since then `main`
gained:

- #958 (#956) run the goal evaluator in the agent's work dir and count late usage
- #959 (#957) post a goal's status change to the room chat

No schema migrations were added in this range.

## Task

Cut a release that carries the goal status posts and the evaluator fix.

## Action

- `packages/cluster/pyproject.toml`: `0.27.0` → `0.28.0` (minor: new feature).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.28.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
