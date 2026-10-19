# docs(readme): add engine install commands and the project-then-room flow (#902)

- Commit: `297f44f` (297f44f962cb82aafd01f3d80652ac990e59d868)
- Author: Changyong Um
- Date: 2026-10-19T15:34:49+09:00
- PR: #902

## Situation

All three READMEs (English root, Korean root, PyPI `packages/cluster`) had
prerequisites that were incomplete or misleading: they listed `codex` or `pi`
as a requirement without explaining how to install them, mentioned Python 3.12+
even though uv manages Python automatically, and told users to "create a room"
as the very first step — whereas the actual UI requires creating a project
first. New users following the instructions would reach dead ends before writing
their first message.

## Task

- Remove the Python 3.12+ prerequisite line (uv supplies Python).
- Add explicit `npm` install commands for both supported engine CLIs (`@openai/codex` and `@earendil-works/pi-coding-agent`).
- Mention that engine setup can be deferred; the app surfaces the same commands and a **Detect again** button.
- Correct the first-run order from "create a room" to "create a project, then a room inside it".
- Keep all three READMEs in sync (EN, KO, PyPI `packages/cluster/README.md`).

## Action

- `README.md` (lines ~20–42): replaced the prerequisite paragraph; added a new
  `npm i -g` code block with both CLI options and the defer-friendly note; fixed
  step 2 of the usage list to say "Create a project and a room in it".
- `README.ko.md` (lines ~20–42): Korean equivalent — same structural changes;
  updated step 2 to "프로젝트와 그 안의 방을 만든 뒤" and added the **다시
  감지** button note.
- `packages/cluster/README.md` (lines ~7–38): same engine install block and
  corrected project-first usage prose added to the PyPI package README.
- No code changes, no tests affected; 8 pre-existing usability tests passed
  (`uv run --directory packages/cluster pytest tests/test_cli_usability.py`).

## Decisions

No plan file was found for this change; rationale is inferred from the diff and
commit body.

- **Why drop "Python 3.12+" from prerequisites?** uv's `tool install`
  transparently provisions the correct Python version. Listing Python as a
  separate prerequisite creates a false dependency and extra friction for users
  who use uv precisely to avoid managing Python versions.
- **Why add npm commands rather than linking to each CLI's own docs?**
  Copy-pasteable commands lower the activation energy significantly. A link
  requires the reader to open another page, find the right install step, and
  come back. The install commands are stable and short enough to inline.
- **Why mention the "Detect again" button?** Users who hit the README before
  installing Node.js should not feel forced to restart; the app handles late
  engine setup gracefully. Making this explicit removes a likely support
  question.
- **Project-before-room order**: The UI enforces this order; the README did not
  reflect it. Correcting it prevents confusion when the "Create room" call to
  action doesn't immediately appear on first load.

Assumption: the `@earendil-works/pi-coding-agent` npm package name is the
canonical install path for Pi. If that changes, the README install block will
need updating.

## Result

All three README files now accurately describe the onboarding path a new user
will actually encounter. The engine install section gives copy-pasteable `npm`
commands with a note that installation can happen after the server is running.
The usage steps reflect the real project → room → agent sequence. 8 CLI
usability tests continue to pass.
