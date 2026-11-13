# chore(release): bump anygarden to 0.29.0

- Author: Claude (assisted, user-directed)
- Date: 2026-11-13
- PR: — (release)

## Situation

`anygarden 0.28.0` was tagged on 2026-10-28 at `0cf5dfe`. Since then `main`
gained 40 commits, including:

- Goals: optional token budget, reports in the main chat with deliverables,
  evaluator and cycle-message fixes (#963, #964, #969)
- Workspace/peers: multi-approval continuation, remove a room member,
  ask_peer with room files, final report location (#969, #978, #980, #982)
- Personal API keys, scripting guide and a user MCP endpoint (#988–#990)
- `/clear` resets agents' engine sessions in a room (#986)
- Agent links: ask linked agents from any room at any depth, live overview
  home screen, engine-logo circles and alert badges (#1006–#1038)
- Chat: any-type composer uploads, linked local files, archived-room file
  retention, question chain rows (#1013–#1029)
- Sidebar: per-user project order and drag by row (#995, #1004); resizable
  sidebar (#967); task rail one-line rows with a detail dialog (#1026)

Schema migrations 102–107 were added in this range; nodes upgrade on start.

## Task

Cut a release that carries these changes.

## Action

- `packages/cluster/pyproject.toml`: `0.28.0` → `0.29.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.29.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
