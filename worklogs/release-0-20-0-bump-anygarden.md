# chore(release): bump anygarden to 0.20.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-15
- PR: — (release)

## Situation

`anygarden 0.19.0` was tagged on 2026-10-14 at `f0c5291`. Since then `main`
gained user-facing federation work and fixes without a version change:

- #840 (#839) simplify connecting two nodes — node card + invite code
- #845 (#842) two-year certificates; an expired one stops federation, not the node
- #844 (#841) list shared rooms a global admin participates in
- #846 (#843) pin a per-node root and renew the TLS leaf automatically
- #838 (#837) README update for the 0.19 single distribution

No schema migrations were added in this range.

## Task

Cut a release that carries the federation changes, since connecting nodes
through the 0.19 UI invite flow always failed.

## Action

- `packages/cluster/pyproject.toml`: `0.19.0` → `0.20.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.20.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
