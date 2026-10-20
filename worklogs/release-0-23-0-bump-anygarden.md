# chore(release): bump anygarden to 0.23.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-20
- PR: — (release)

## Situation

`anygarden 0.22.0` was tagged on 2026-10-19 at `625481b`. Since then `main`
gained workspace file management, machine moves, room archiving, a startup
DB backup and several agent/UI changes:

- #910 / #913 (#908) instructions kept on the machine's disk with revisions; one Workspace tab
- #918 (#914) large work files and folder zips over HTTP with one-time tickets
- #909 (#907) / #919 (#915) sticky machine placement; moving an agent moves its work files
- #917 (#911) delete a room with run history by archiving it
- #923 (#920) integrated node backs up the DB and applies pending migrations on start
- #906 (#901) delegate_to_subroom; #904 (#899) text /delegate path removed
- #905 (#900) ask_peer settles by the peer's turn state; #895 (#892) idle-turn timeout
- #896 (#893) work duration on replies; #916 (#912) / #922 (#921) responding indicators
- #903 (#902) onboarding; #894 (#889) / #898 (#897) settings and room header trims

Schema migrations in this range: 094 sticky agent placement, 095 agent
instruction revisions, 096 machine transfers.

## Task

Cut a release that carries these features and the three new migrations.

## Action

- `packages/cluster/pyproject.toml`: `0.22.0` → `0.23.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.23.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
