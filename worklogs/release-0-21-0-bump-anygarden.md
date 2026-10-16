# chore(release): bump anygarden to 0.21.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-16
- PR: — (release)

## Situation

`anygarden 0.20.0` was tagged on 2026-10-15 at `e1d40ae`. Since then `main`
gained:

- #849 (#848) serve another server as a machine while keeping the local node
- #850 (#848) README: lend a node's host to another server
- #852 (#851) machine badge on agents placed on a remote machine

No schema migrations were added in this range.

## Task

Cut a release that carries the remote-machine hosting feature.

## Action

- `packages/cluster/pyproject.toml`: `0.20.0` → `0.21.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.21.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
