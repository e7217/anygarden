# chore(release): bump anygarden to 0.24.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-26
- PR: — (release)

## Situation

`anygarden 0.23.0` was tagged on 2026-10-20 at `d13d5f8`. Since then `main`
gained:

- #929 (#928) split the sidebar's projects and agents into resizable, collapsible sections
- #932 (#927) update the integrated node from the web UI and restart it in place

No schema migrations were added in this range.

## Task

Cut a release that carries the in-place web update and the sidebar split.
0.24.0 is also the first version an installed 0.24 node can be updated
*to* from the web UI once a newer release exists.

## Action

- `packages/cluster/pyproject.toml`: `0.23.0` → `0.24.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.24.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
