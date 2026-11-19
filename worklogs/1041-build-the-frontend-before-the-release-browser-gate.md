# fix(ci): build the frontend before the release browser gate

- Commit: `8161262` (816126247d8951515ff9ed99db79a039b0e2f91f)
- Author: Changyong Um
- Date: 2026-11-13T11:17:35+09:00
- PR: #1041

## Situation

After the Vitest flake fix was committed, the `anygarden-v0.29.0` release gate
was re-run. Vitest passed this time, but the browser E2E step timed out waiting
for its web server. Since #1002, Playwright on CI serves the production bundle
with `vite preview` rather than the Vite dev server. `ci.yml` already built the
frontend before the E2E step, but `release.yml` still ran E2E **before** the
build step, leaving no bundle for `vite preview` to serve.

## Task

Move the frontend build step in `release.yml` to precede the Playwright E2E
step, so the bundle exists when `vite preview` tries to start it.

Constraints:
- Must not change `ci.yml` (already correct).
- Must not alter the build command itself.
- Must not reorder any steps other than the build step.

## Action

- `release.yml:35–39`: added a `Build frontend (typecheck + bundle)` step
  immediately before the `Install Playwright Chromium` step (the existing build
  step was at lines 55–59, after the artifact-upload step).
- `release.yml:55–58`: removed the now-redundant build step that had been
  positioned after the artifact-upload step.
- `worklogs/1041-task-timeline-test-flake.md`: updated the existing worklog to
  reflect the full scope of the fix (both the Vitest flake and the CI ordering
  regression) and corrected the result line to note that `anygarden-v0.29.0`
  had published nothing before this fix.

## Decisions

- **Move existing step vs. duplicate it**: moving the step is cleaner — a
  duplicate would run the build twice and inflate CI time — rejected.
- **Fix `release.yml` only vs. also refactoring `ci.yml`**: `ci.yml` was already
  correct; touching it would add noise and risk — rejected.
- **Re-tag vs. cut a new patch release**: the original tag had published nothing
  (gate failed before the build job ran), so re-pointing the tag is equivalent
  to a fresh release without bumping the version number; a new patch would
  consume a semver slot for a CI-only fix — re-tagging chosen.

Assumption: `vite preview` will continue to require a pre-built bundle (i.e.,
the `dist/` directory must exist before `vite preview` starts). If a future
version of Vite supports on-demand serving without a pre-built bundle, this
ordering requirement may become unnecessary.

## Result

`release.yml` now builds the frontend bundle before the E2E step. The
`anygarden-v0.29.0` tag is re-pointed at this commit. The release gate completes
cleanly and the build-and-release job can proceed to publish artifacts.
