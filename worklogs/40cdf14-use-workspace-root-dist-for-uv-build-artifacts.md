# fix(release): use workspace-root dist/ for uv build artifacts

- Commit: `40cdf14`
- Author: Changyong Um
- Date: 2026-05-20
- PR: —

## Situation

The release workflow added in #387 (PyPI rename) was never exercised against a real tag until v0.7.0. When `drhub-v0.7.0`, `drmachine-v0.7.0`, `dragent-v0.7.0` were pushed, all three workflow runs failed within 11–15 seconds at the Create GitHub Release step with `no matches found for packages/cluster/dist/*`. The workflow ran `uv build` from `packages/<pkg>/` and expected outputs under `packages/<pkg>/dist/`, but uv writes workspace-member build artifacts to the workspace root `dist/` regardless of the invocation cwd.

## Task

- Align the workflow's build + upload paths with where uv actually writes artifacts (workspace root `dist/`).
- Keep the rest of the workflow (tag parsing, pyproject version check, prev-tag resolution, release notes) untouched — the failure was localized to two paths.
- Make the build command robust against future workspace expansion: select the package explicitly by name rather than relying on cwd.

## Action

- `.github/workflows/release.yml` Build step — drop `working-directory: packages/${PKG}`, run from repo root, invoke `uv build --package "dr${NAME}"`. Output lands in `dist/dr${NAME}-${VERSION}*`.
- `.github/workflows/release.yml` Create GitHub Release step — upload from `dist/dr${NAME}-${VERSION}*` instead of `packages/${PKG}/dist/*`. Drop the now-unused `PKG="..."` assignment in this step.
- Verify pyproject version step (still uses `packages/${PKG}/pyproject.toml`) left alone — pyproject.toml actually does live at that path; only build *output* moved.

Verified locally: `rm -rf dist/ && uv build --package drhub` from repo root produces `dist/drhub-0.7.0.tar.gz` + `dist/drhub-0.7.0-py3-none-any.whl`.

## Decisions

- **`uv build --package <name>` from root** vs. `uv build --out-dir dist` from the package dir — both produce per-package artifacts in a known location. Chose `--package` because:
  - It avoids the per-package working-directory dance — simpler workflow.
  - It works without assuming `--out-dir` defaults stay stable across uv versions.
  - Explicit package selection means the workflow doesn't break if someone later restructures `packages/*` directories; the dist name is canonical, the path is not.
- **Glob pattern `dr${NAME}-${VERSION}*` over an explicit list** — covers both `.tar.gz` and `.whl` and any future format additions without needing to enumerate.
- **Did NOT add a backup step or retry logic** — the failure was deterministic and now structurally impossible (uv build under that flag emits into `dist/`, the glob always picks them up). Adding defensive code for a fixed bug would just be noise.

The original workflow assumed `uv build` inside a subdir would write to that subdir's `dist/`. That assumption is wrong for uv workspaces but unverified at #387 review time because no tag had been pushed yet — the failure exposed the gap.

## Result

- Workflow build/upload paths now match uv's actual output location.
- Pending: re-fire the three v0.7.0 releases. Options are (a) delete + re-push the tags so the workflow re-runs against the fixed `main`, or (b) leave the failed runs and tag a `v0.7.1` patch that re-triggers cleanly. The fix has to ship to `main` first either way.
- Future tag pushes (`drhub-v*`, `drmachine-v*`, `dragent-v*`) will now build + upload cleanly without manual intervention.
