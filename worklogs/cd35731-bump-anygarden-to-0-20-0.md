# chore(release): bump anygarden to 0.20.0

- Commit: `cd35731` (cd35731f5a28702f145f6f5a6407f3ba4679b9a4)
- Author: Changyong Um
- Date: 2026-10-15
- PR: — (release)

## Situation

PyPI `anygarden 0.19.0` was cut on 2026-08-09. Since then `main` has
accumulated substantial new work including federation support (TLS leaf
renewal, simplified node connect, per-node root pins), agent runtime
consolidation (agent package merged into `anygarden`), sidebar and thread UI
refinements, chat fixes, and CI gate changes. The version string in
`packages/cluster/pyproject.toml` still read `0.19.0`, meaning any PyPI
release attempt from `main` would collide with the already-published package.

## Task

- Bump `anygarden` (cluster) 0.19.0 → **0.20.0** in `packages/cluster/pyproject.toml`.
- Update `uv.lock` to match the new version.

## Action

- `packages/cluster/pyproject.toml:7`: `version` changed from `"0.19.0"` to `"0.20.0"`.
- `uv.lock:51`: `anygarden` package version entry updated from `"0.19.0"` to `"0.20.0"`.

## Decisions

N/A — mechanical change. The version bump follows the same pattern
established in the 0.19.0 release (c3957d1): both `pyproject.toml` and
`uv.lock` must be updated together because the `uv lock --check` gate
(added in Phase 0, #559/#560) fails if they diverge. Choosing **minor**
(0.20.0 rather than 0.19.x) reflects that the release includes
user-visible features (federation, agent runtime consolidation, sidebar
actions) rather than only bug fixes.

## Result

`anygarden` version string on `main` is now `0.20.0`, unambiguously
distinct from `0.19.0` on PyPI. A subsequent `uv publish` from this
commit can proceed without a version collision. No behavioural changes —
this commit touches version metadata only.
