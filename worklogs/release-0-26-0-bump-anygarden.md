# chore(release): bump anygarden to 0.26.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-27
- PR: — (release)

## Situation

`anygarden 0.25.0` was tagged on 2026-10-26 at `02217bb`. Since then `main`
gained:

- #942 (#939) call recurring goals "routines" and campaigns "goals" in the UI
- #943 (#940) open routine and goal forms from `/routine` and `/goal`
- #944 (#938) let agents look over and change the workspace for the person who asked

Schema migration in this range: 100 workspace agent tools.

## Task

Cut a release that carries the routine/goal naming, the slash-command forms
and the workspace agent tools with their migration.

## Action

- `packages/cluster/pyproject.toml`: `0.25.0` → `0.26.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.26.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
