# ci(release): add tag-triggered GitHub Release workflow

- Commit: `d2541aa` (d2541aa8000513bb047ddd5d37c08a13d292de1d)
- Author: Changyong Um
- Date: 2026-05-06T14:28:12+09:00
- PR: —

## Situation

PyPI artifacts for `doorae-{agent,cluster,machine}` 0.5.0 and 0.5.1 already exist (uploaded 2026-04-28 via the manual `make release-*` flow), but the corresponding GitHub Releases were never created — `gh release list` stops at v0.4.1. The repo had only `ci.yml` (test workflow); nothing in `.github/workflows/` reacted to tag pushes. Each retroactive Release would otherwise need to be hand-rolled (`uv build`, manual upload, manual `gh release create`), and the same toil would repeat for every future tag. PRs #340 (CHANGELOG backfill) and #341 (0.5.1 → 0.6.0 bump) close the docs and version gaps; this PR closes the automation gap so the upcoming retroactive 0.5.0 / 0.5.1 tags + the fresh 0.6.0 tag publish via push instead of by hand.

## Task

Add a workflow that, on `doorae-{agent,cluster,machine}-v*` tag push:
- parses package + version from the tag,
- verifies `pyproject.toml` version matches the tag (fail-fast on drift),
- builds sdist + wheel via `uv build` for the matching package,
- creates a GitHub Release with auto-generated notes scoped to the package's own tag history,
- attaches the dist artifacts to the Release.

Constraints:
- PyPI publish must stay manual (per chosen scope: 1+2 = "release auto-create + dist artifact attach", PyPI excluded). Do not add `twine upload`, do not request a `PYPI_API_TOKEN`.
- Auto-generated notes must scope to the same package's prior tag — interleaved per-package tags would otherwise produce confusing cross-package diffs.
- Workflow must be additive only — must not modify `ci.yml`, `pyproject.toml`, or any source.
- Permissions: minimum needed (`contents: write` for `gh release create`).

## Action

New file `.github/workflows/release.yml` (84 lines):

- `on.push.tags`: three glob patterns, one per package, matching the existing `doorae-{pkg}-v*` tag scheme already used for v0.2.0–v0.4.1.
- `permissions: contents: write` at workflow level — no other scopes granted.
- Single job `build-and-release` on `ubuntu-latest`. Step sequence:
  1. `actions/checkout@v4` with `fetch-depth: 0` so `git tag --list` sees the full per-package tag history later.
  2. `Parse tag` — bash parameter expansion `PKG="${TAG#doorae-}"; PKG="${PKG%-v*}"; VERSION="${TAG##*-v}"`. `PKG="${PKG%-v*}"` strips the `-v...` suffix (handles `-v1.0.0-rc1`-style versions correctly: only the first `-v` is treated as the boundary). Outputs `package` and `version`.
  3. `Verify pyproject version matches tag` — `grep` + `sed` on `packages/<pkg>/pyproject.toml` line 7-ish, `exit 1` with `::error::` annotation if they differ. Catches the "tag points at a commit whose pyproject says something else" footgun.
  4. `astral-sh/setup-uv@v3` with the same cache key as `ci.yml` for consistency.
  5. `uv build` in `packages/<pkg>/` after `rm -rf dist/` — clean build.
  6. `Resolve previous tag for same package` — `git tag --list "doorae-${PKG}-v*" --sort=-version:refname | grep -vx "${CURRENT}" | head -n1`. Empty output handled (first release of a package).
  7. `Create GitHub Release` — bash array `ARGS` with the tag, title `doorae-<pkg> v<version>`, `--generate-notes`, conditional `--notes-start-tag <prev>`, then `packages/<pkg>/dist/*` as positional asset args. `GH_TOKEN: ${{ github.token }}` for auth.

Local validation:
- `python3 -c "import yaml; yaml.safe_load(...)"` on the file → YAML OK.
- Tag-parse logic verified against three sample inputs:
  - `doorae-cluster-v0.6.0` → `cluster`, `0.6.0`
  - `doorae-machine-v0.5.1` → `machine`, `0.5.1`
  - `doorae-agent-v1.0.0-rc1` → `agent`, `1.0.0-rc1` (multi-segment version preserved)

## Decisions

Tag pattern options weighed:

- **A. Single trigger `v*`** — global version tag, everything releases together. Rejected: existing convention (and active workflow design above) is per-package tags `doorae-<pkg>-v<X.Y.Z>` since the v0.2.0 cycle. Switching now would orphan the prior tag scheme and force consumers to map tags differently. Per-package tags also let one package release independently when only its diff warrants it.
- **B. Three explicit patterns `doorae-{agent,cluster,machine}-v*` (chosen)** — matches existing convention exactly, makes the trigger surface readable, and constrains the workflow to known package names so a stray `experimental-pkg-v0.0.1` tag wouldn't accidentally fire it.
- **C. Wildcard `doorae-*-v*`** — simpler, but accepts any `doorae-foo-v…` shape and would route to a missing `packages/foo/` directory, failing late (during `uv build`) instead of early (at the trigger). Rejected for the same fail-late reason.

What tipped toward B: explicit allow-list is the cheapest extra typing for the strongest invariant (workflow only fires for the three real packages).

`--notes-start-tag` scoping: GitHub's default behavior for `--generate-notes` is "previous Release of any kind." With per-package tags interleaved (`agent-v0.5.1` ↦ `cluster-v0.5.1` ↦ `machine-v0.5.1`), running `--generate-notes` on `cluster-v0.6.0` without a start tag would diff against `machine-v0.5.1` (the most recent any-package release), pulling machine commits into cluster's release notes. The `git tag --list "doorae-${PKG}-v*" --sort=-version:refname | grep -vx "${CURRENT}" | head -n1` resolution finds the immediately previous same-package tag deterministically. `version:refname` sort handles semver correctly for the project's `vX.Y.Z` convention; falls apart for unconventional version strings, but the existing tag corpus is well-formed.

Pyproject-version verification before build: catches the case where someone tags a commit but forgets to bump the pyproject — would otherwise produce a wheel whose `PKG-INFO` version disagrees with the tag, confusing PyPI consumers later. `grep + sed` on the first `version = ` line is brittle (would break if a future pyproject puts `version =` inside a different table), but works for the current Hatchling layout where `[project].version` is the only such line.

PyPI publish explicitly excluded: matches the scope the user picked (1+2 = "release auto-create + dist artifact attach"). Adding `twine upload` would require provisioning `PYPI_API_TOKEN` (or trusted publishing via OIDC), which is a separate decision the user deferred. Manual `make release-*` continues to handle PyPI uploads — the workflow merely makes sure that GitHub Releases aren't manual at the same time.

`fetch-depth: 0`: needed so `git tag --list` inside the workflow sees the full tag corpus (shallow clones omit tags by default). Costs ~seconds on a small repo; fine for a release workflow that runs only on tag push.

Assumptions, if violated → revisit:
- `pyproject.toml` keeps `version = "..."` on its own line in `[project]` — if Hatchling moves to dynamic versioning, the `grep + sed` check breaks and should switch to `python -c "import tomllib; ..."`.
- `--generate-notes` produces useful output. If the auto-generated notes turn out to be too thin (e.g. squash commits without PR linkage), switching to a `--notes-file` pre-built from the relevant CHANGELOG section is the natural follow-up.
- Tags are always pushed *after* the matching pyproject bump lands on `main`. If someone tags a feature branch, the version-match check still passes (since the branch has the bump), but the Release would publish artifacts off a non-main commit. Acceptable for now — `gh release create` still records the source SHA — but worth flagging if release branches become a thing.

## Result

- `.github/workflows/release.yml` exists; CI run on tag push will produce a GitHub Release with sdist + wheel attached.
- No CI / source / config changes — purely additive.
- First exercise: the planned retroactive `doorae-{agent,cluster,machine}-v0.5.0` and `-v0.5.1` tags (after #340 / #341 land), then the fresh `-v0.6.0` tags. PyPI continues to be populated via `make release-*` as today.
- Pending: PRs #340 and #341 land; then push retroactive 0.5.0 / 0.5.1 tags + 0.6.0 tag to trigger the new workflow; verify the Release pages render correctly with `--generate-notes` output and dist asset attachments.
