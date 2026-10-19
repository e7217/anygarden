# chore(release): bump anygarden to 0.22.0

- Commit: `35675ee` (35675ee065c0f40f6e3664d14e69649d87b9cc95)
- Author: Changyong Um
- Date: 2026-10-19T11:11:37+09:00
- PR: —

## Situation

`anygarden 0.21.0` was tagged on 2026-10-16 at `d820192`. Since then `main` accumulated a significant batch of new features and fixes: file attachment flows (#855, #877, #878, #879, #883), a live work trace on agent replies (#886), agent/UI fixes (#861–#871, #876), and a README trim (#884). No schema migrations were added in this range, making it safe to cut a minor release.

## Task

Cut a minor release (`0.22.0`) that ships the file-attachment and work-trace features to PyPI via the existing `release.yml` pipeline. The version bump must be applied to `packages/cluster/pyproject.toml` and reflected in `uv.lock`.

## Action

- `packages/cluster/pyproject.toml` line 7: `version = "0.21.0"` → `version = "0.22.0"`
- `uv.lock` package entry for `anygarden`: `version = "0.21.0"` → `version = "0.22.0"` (updated by `uv lock`)
- `worklogs/release-0-22-0-bump-anygarden.md`: release notes written alongside the bump commit

## Decisions

- **Minor bump (0.21 → 0.22) over patch**: the range includes new user-facing features (file attachments, work trace, agent presence overlay) — semantic versioning warrants a minor increment.
- **Single commit on a release branch**: grouped the version bump, lock update, and release notes into one atomic commit so the tag lands on a clean, reviewable state; avoids a multi-commit release branch that could carry noise.
- **`release.yml` triggered by tag, not branch merge alone**: the existing CI pipeline gates on a `anygarden-v*` tag after the PR merges, so no pipeline changes were needed.
- Assumption: no breaking API changes were introduced in the feature range; if any turn out to break downstream consumers, a patch `0.22.1` would be appropriate rather than a major bump.

## Result

PR #890 (`chore/release-0.22.0` → `main`) opened at https://github.com/e7217/anygarden/pull/890. Merging and tagging `anygarden-v0.22.0` will trigger `release.yml` (browser gate → build → GitHub Release → PyPI Trusted Publishing). No migrations pending; no tests changed.
