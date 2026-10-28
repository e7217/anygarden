# chore(release): bump anygarden to 0.27.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-28
- PR: — (release)

## Situation

`anygarden 0.26.0` was tagged on 2026-10-27 at `b8b780e`. Since then `main`
gained:

- #949 (#946) turn off Codex built-in goals so goals never live outside anygarden
- #950 (#947, #948) let an agent draft `/goal` and `/routine` for one-click creation
- #952 (#951) save the workspace switches on an agent
- #954 (#953) keep earlier no-gain hypotheses in the goal cycle brief

Schema migration in this range: 101 workspace draft requests.

## Task

Cut a release that carries agent-drafted goals/routines, the goal cycle
brief change and two fixes, with their migration.

## Action

- `packages/cluster/pyproject.toml`: `0.26.0` → `0.27.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.27.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
