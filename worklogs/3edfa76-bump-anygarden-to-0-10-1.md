# chore(release): bump anygarden to 0.10.1

- Commit: `3edfa76`
- Author: Changyong Um
- Date: 2026-06-22
- PR: release/anygarden-v0.10.1

## Situation

A QA pass surfaced eleven findings against the running service; after a
code-grounded verification six were confirmed real defects, all inside the
cluster (`anygarden`) package. Those fixes shipped to `main` as three squash
merges — input validation (#471 / PR #474), search-500 FTS bootstrap +
favicon fallback (#473 / PR #475), and room-create 404 on bad project_id
(#472 / PR #476). `main` was at `f63721c` with the fixes merged but no
release cut, so the corrected `anygarden` was not yet published to PyPI. The
last release was `anygarden 0.10.0` (#467); `agent` and `machine` sit at
0.9.0 and were untouched by these fixes.

## Task

- Bump only the cluster distribution `anygarden` from 0.10.0 → 0.10.1 (patch:
  the three changes are bug fixes, no API additions).
- Leave `anygarden-machine` / `anygarden-agent` at 0.9.0 — no code change, so
  no empty re-release.
- Record the three fixes in `packages/cluster/CHANGELOG.md` and resume the
  changelog trail (v0.9.x / v0.10.0 had shipped notes via the tag-triggered
  GitHub Release rather than the file).
- Drive the release through the established flow: release branch → PR (CI
  gates) → squash to main → push tag `anygarden-v0.10.1`, which the
  `release.yml` workflow turns into a built sdist+wheel, a GitHub Release, and
  a Trusted-Publishing (OIDC) upload to PyPI.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.10.1"`.
- `packages/cluster/CHANGELOG.md` — new `## v0.10.1 (2026-06-22)` section under
  `## Unreleased` summarising the three fixes (#471, #472, #473) plus a short
  note explaining the v0.9.x/v0.10.0 changelog gap.
- `uv.lock` re-resolved to `anygarden==0.10.1` locally; the file is untracked
  (git-ignored) so it is not part of the commit — CI re-resolves on its own.
- Work done in an isolated `worktrees/release-anygarden-v0.10.1` worktree cut
  from `main` so the unrelated `design-sync/anygarden-ui` checkout in the
  primary worktree stayed undisturbed.

## Decisions

- **0.10.1 (patch) over 0.11.0 (minor)** — although the search fix restores a
  user-facing capability, it is a regression repair (the endpoint was meant to
  work and 500'd on a supported bootstrap path), not a new feature. Patch
  versioning matches the change's nature; the prior bug-only cut `0.9.1`
  (#412) set the same precedent.
- **Cluster-only bump, not the historical lockstep** — earlier releases bumped
  all three packages together, but those carried at least incidental diffs.
  Here `agent`/`machine` have literally no change since 0.9.0, so a lockstep
  bump would publish two empty releases. The `release.yml` tag scheme is
  per-distribution (`anygarden-v*`, `anygarden-machine-v*`, `anygarden-agent-v*`),
  so an independent cluster tag is fully supported.
- **Resume CHANGELOG.md rather than backfill 0.9.x/0.10.0** — the missing
  intermediate entries were intentionally carried by GitHub Release
  auto-notes; reconstructing them now would be guesswork. A one-line note
  records why the file jumps 0.8.0 → 0.10.1.
- **Release branch + PR over a direct tag on main** — keeps the version bump
  under the same CI gates every other change passes, and the tag is pushed
  only after the bump is on `main` so `release.yml`'s "pyproject version ==
  tag" guard holds against the merged commit.

## Result

- `anygarden` pinned to 0.10.1 with a changelog entry covering the three
  shipped fixes; `agent`/`machine` untouched at 0.9.0.
- Release branch `release/anygarden-v0.10.1` raised as a PR for CI validation;
  on merge, tag `anygarden-v0.10.1` triggers the build + GitHub Release + PyPI
  Trusted-Publishing upload.
