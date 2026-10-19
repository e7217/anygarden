# chore(release): bump anygarden to 0.22.0

- Author: Claude (assisted, user-directed)
- Date: 2026-10-19
- PR: — (release)

## Situation

`anygarden 0.21.0` was tagged on 2026-10-16 at `d820192`. Since then `main`
gained file attachments, a live work trace and several agent/UI fixes:

- #855 (#854) attach files an agent writes during a turn to its reply
- #877 (#872) attach files to the composer by drag-and-drop and paste
- #878 (#873) attach images to your own messages through room artifacts
- #879 (#874) let agents see the files a message attaches
- #883 (#882) zoom and pan attached images in the image viewer
- #886 (#880) show what an agent is doing and keep a work trace on its reply
- #862 (#856) agent replies fill the chat column
- #871 (#869) agent presence overlaid on the avatar
- #861 (#857) always write AGENTS.md; #863 (#858) codex sandbox can write the outbox;
  #870 (#866) agents get the room conversation they missed
- UI fixes #865, #868, #876; README trim #884

No schema migrations were added in this range.

## Task

Cut a release that carries the attachment and work-trace features.

## Action

- `packages/cluster/pyproject.toml`: `0.21.0` → `0.22.0` (minor: new features).
- `uv lock` to record the new workspace version.

## Result

Tagging `anygarden-v0.22.0` on the merged commit triggers `release.yml`
(browser gate → build → GitHub Release → PyPI Trusted Publishing).
