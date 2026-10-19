# docs(readme): trim the README to intro, install and usage (#881)

- Commit: `cfd2a4d` (cfd2a4de6fdddcfea516c61c53ee8fec975d8b6d)
- Author: Changyong Um
- Date: 2026-10-19T09:51:07+09:00
- PR: #881

## Situation

The root README had grown to 162 lines. Before a new user could try anything, they had to get past three equally weighted install paths (`anygarden start`, server + machine daemon, `make dev`), an 0.18 → 0.19 upgrade guide, a gateway retirement notice and a package extras table. The page published to PyPI is a different file, `packages/cluster/README.md`, and its Quick Start still led with `anygarden server init` → `anygarden server`, so GitHub and PyPI gave different first steps.

## Task

- Cut the root README to four sections (intro, install, use, learn more) with one path: `anygarden start`.
- Add a Korean translation with the same structure and commands.
- Lead the PyPI README with the same install and usage, and keep its CLI reference (`test_cli_usability.py` checks it).
- Include a screenshot of a real room.
- Drop content that is stale or already documented elsewhere instead of moving it to new files.
- Keep `CONTRIBUTING.md` working: it linked to three README anchors that would disappear.

## Action

- `README.md`: rewritten to 51 lines. It has badges, a language switch, a one-line pitch, three feature bullets, a screenshot, Install (prerequisites + `uv tool install "anygarden[server,agent]"`), Use (`anygarden start`, three steps, `anygarden stop`), Learn more and License.
- `README.ko.md`: new. Korean version with identical commands; links back to the English README.
- `packages/cluster/README.md`: replaced the old Quick Start with Install, Use, an Extras table and a short "other entry points" block. The block keeps `--name demo-agent` and `--server ws://localhost:8000`, which the test asserts. All links and the image use absolute GitHub URLs so they resolve on pypi.org, including the `docs/agent/README.md` link in the CLI reference.
- `CONTRIBUTING.md`: states its prerequisites (Python 3.12+, uv, Node 20+) and why a bare `uv sync` is wrong. The extras link now points to `packages/cluster/README.md#extras`.
- `docs/assets/room.png`: a 2560×1800 capture of a real `codex-cli` agent reviewing a retry function. It was taken on an isolated integrated node with a demo account.

## Decisions

- **One install path in the README** rather than the old three-way list. `anygarden start` makes the first sign-up the admin and binds the host as a machine automatically, so a new user gets an agent reply without tokens or registration. The other paths are needs that come after a first try.
- **Delete instead of move** (user decision). Multi-machine setup is already covered by `packages/cluster/docs/machine/operations.md` and host lending by `docs/runbook/local-node.md`, so the README only links to them. The gateway notice exists in `docs/runbook/gateway-removal.md`. The 0.18 upgrade guide is stale now that 0.21 is out. It has no copy elsewhere and survives only in git history; this was accepted.
- **Duplicate about 15 lines between the root and PyPI READMEs** rather than pointing `readme` at `../../README.md` or adding hatch-fancy-pypi-readme. Pointing outside the project root risks sdist packaging, and the root README's relative links would all break on PyPI. A build plugin adds a dependency for little gain.
- **Real screenshot over an e2e-stubbed one**: a product image should show actual behaviour, and it is rarely regenerated, so automation was not worth it.
- Revisit if: the two READMEs drift apart, in which case add a test that compares their command blocks; or the image URL on `main` moves.

## Result

- The root README dropped from 162 to 51 lines. It and the Korean version show the same commands.
- A link and anchor check over the four changed markdown files found 0 broken links. Absolute `blob/main` URLs were mapped to local paths for the check.
- `tests/test_cli_usability.py`: 8 tests passed.
- `uv build --package anygarden --sdist` and `twine check` passed, so the PyPI long description renders.
- Live check: `anygarden start` on an isolated data dir, then sign-up, room, codex agent and mention produced a real reply, which is the screenshot.
- Pending: the raw image URL resolves only after merge to `main`. PyPI shows the new README from the next release.
