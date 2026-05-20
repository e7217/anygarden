# chore(release): bump drhub / drmachine / dragent to 0.7.0

- Commit: `0106ccd` (0106ccdb…)
- Author: Changyong Um
- Date: 2026-05-20
- PR: —

## Situation

The three workspace packages were pinned at 0.6.0 (cut 2026-05-06 under the legacy `doorae-*` PyPI names) and accumulated 14+ PRs since: OpenHands V1 SDK migration (#355), gateway/Ollama wiring (#359), claude-code deprecation (#382), orchestrator fallback nominate (#389), shared file references (#376, #378), sidebar unread indicators (#385), machine online status (#383), plus the PyPI rename to dr-prefixed names (#387). Each package CHANGELOG had an "Unreleased" section but no shipping version. The renamed distributions had no first release on PyPI under `drhub` / `drmachine` / `dragent`.

## Task

- Bump all three package versions in lockstep from 0.6.0 → 0.7.0 — coordinated workspace release matches prior convention (v0.5.1 / v0.6.0 also moved all three together).
- Fill each CHANGELOG's `## Unreleased` section into a `## v0.7.0 (2026-05-20)` entry summarising features / fixes since v0.6.0, scoped to that package's actual diff (don't cross-pollinate cluster-only PRs into the agent changelog).
- Verify all three packages still build under the new versions via `uv build`.
- Leave a fresh empty `## Unreleased` heading so future commits have somewhere to land.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.7.0"`.
- `packages/machine/pyproject.toml:7` — `version = "0.7.0"`.
- `packages/agent/pyproject.toml:7` — `version = "0.7.0"`.
- `packages/cluster/CHANGELOG.md` — new `v0.7.0 (2026-05-20)` section with Release infrastructure (#387), Features (#355, #359, #382, #376/#378, #385/#386, #383/#384), Fixes (#389, #379/#380, #377, #362/#363, #364/#365, #371, #369/#370), Docs (#381 + research note).
- `packages/agent/CHANGELOG.md` — new `v0.7.0` section with rename (#387), OpenHands adapter + runtime tools (#355, #377), file references (#376), OpenHands fixes (#375, #372, #366); the existing #345 "cwd assumptions" block was absorbed into v0.7.0.
- `packages/machine/CHANGELOG.md` — new `v0.7.0` section with rename (#387), OpenHands detection (#357/#358), runtime tools (#377), stale auth token fix (#371); existing #350 and #345 blocks absorbed.
- Workspace install reflects the bump: `uv sync --all-packages` produced `drhub==0.7.0`, `drmachine==0.7.0`, `dragent==0.7.0`. `uv build` in each package produced `dist/{drhub,drmachine,dragent}-0.7.0.{tar.gz,whl}` cleanly.

## Decisions

- **0.7.0 (minor) over 0.6.1 (patch)** — OpenHands V1 SDK migration (#355) is a feature-scale change introducing a new in-process engine adapter, plus claude-code deprecation (#382) is a user-visible engine status change. Patch versioning would misrepresent the scope.
- **All three packages bumped together** vs. per-package versioning — v0.5.1 / v0.6.0 history shows the workspace ships coordinated versions even when a package has no functional diff (#387 added the "workspace bump" note for v0.5.1 explicitly). Independent versioning would complicate the `dr*-v*` tag scheme that #387 wired into `release.yml`.
- **Release branch + PR over direct push to main** — the prior version bump (`feat(release): bump all packages to 0.6.0`, worklog `4570fce-…`) went through the PR flow with CI gates. Same path here preserves auditability and lets the release.yml workflow be sanity-checked against the new dr-prefix tag parser (added in #387 but never exercised against a real tag).
- **CHANGELOG sourcing** — entries derived from `git log doorae-{cluster,machine,agent}-v0.6.0..main -- packages/<pkg>`, scoped to each package's actual file changes so cluster-only PRs don't appear in the machine changelog. Where a PR touched multiple packages (e.g. #355 spans agent + cluster), it appears in both with package-appropriate framing.
- **Tag push deferred** — `release/dr-v0.7.0` PR is created but tags are NOT pushed in this commit. Tags trigger the release workflow; pushing them only after the PR merges to main avoids a release built off the release branch HEAD that doesn't match `main`.

## Result

- Three packages on 0.7.0 with consolidated CHANGELOG entries for 14+ PRs since v0.6.0.
- All three `uv build` invocations succeed; built artifacts (`*.tar.gz`, `*.whl`) cleaned from `dist/` after verification.
- Pending: PR merge, then push `drhub-v0.7.0`, `drmachine-v0.7.0`, `dragent-v0.7.0` tags. Each tag will trigger `.github/workflows/release.yml`, which verifies pyproject version matches the tag and creates a GitHub Release with auto-generated notes + attached wheels/sdists.
- PyPI publishing is **not** automated by the workflow (only GitHub Releases) — first PyPI upload under the new `drhub` / `drmachine` / `dragent` names remains a manual step.
