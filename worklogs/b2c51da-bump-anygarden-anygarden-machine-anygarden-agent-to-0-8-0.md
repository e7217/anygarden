# chore(release): bump anygarden / anygarden-machine / anygarden-agent to 0.8.0

- Commit: `b2c51da`
- Author: Changyong Um
- Date: 2026-05-22
- PR: —

## Situation

Main branch reached the fully-rebranded state after #394 merged: Python imports, CLI commands, env vars, on-disk paths, frontend storage keys, and the npm package all moved from `doorae*` to `anygarden*`. The PyPI distributions were already named `anygarden` / `anygarden-machine` / `anygarden-agent` at 0.7.1, but those releases shipped the *previous* baseline where the inner module was still `doorae`. An end user installing `anygarden 0.7.1` and writing `from anygarden import …` would get `ModuleNotFoundError`; the working incantation was still `from doorae import …`. After #394 this is no longer true on main, so the published 0.7.1 wheels lie about what they expose. A new release version is needed to surface the deep rebrand to PyPI.

## Task

- Bump all three workspace packages from `0.7.1` → `0.8.0` in lockstep. Minor bump because the import surface changes — that's a breaking change for any external consumer, and SemVer rules out a patch.
- Document the breaking-change list in each `CHANGELOG.md` under a `## v0.8.0 (2026-05-22)` heading: imports, CLI, env vars, data path, frontend localStorage keys. Make the upgrade obligations explicit; this is the first release where reading the CHANGELOG actually matters.
- Keep the change purely metadata — no source code edits in this commit. The release infrastructure (workflow + tag scheme) introduced in #387/#392 handles the rest.
- Leave a fresh empty `## Unreleased` heading at the top of each CHANGELOG.

## Action

- `packages/cluster/pyproject.toml:7` — `version = "0.8.0"`.
- `packages/machine/pyproject.toml:7` — `version = "0.8.0"`.
- `packages/agent/pyproject.toml:7` — `version = "0.8.0"`.
- `packages/cluster/CHANGELOG.md` — new `v0.8.0 (2026-05-22)` section with a "⚠ Breaking changes — full anygarden rebrand" subsection enumerating import / CLI / env var / data-path / SQLite filename / frontend localStorage migrations. References PR #394 for the diff.
- `packages/machine/CHANGELOG.md` — new `v0.8.0` section. Notes the `doorae_machine` → `anygarden_machine` source-dir + module rename, the CLI command rename, the `DOORAE_*` → `ANYGARDEN_*` env var move, and `~/.doorae/machine/` → `~/.anygarden/machine/`.
- `packages/agent/CHANGELOG.md` — new `v0.8.0` section. Notes the `doorae_agent` → `anygarden_agent` source-dir + module rename, both CLI commands (`doorae-agent`, `doorae-client`) → `anygarden-*`, and the env-var migration.

Verification: `uv sync --all-packages` resolves to `anygarden==0.8.0`, `anygarden-agent==0.8.0`, `anygarden-machine==0.8.0`. `uv build --package <name>` succeeds for all three; produced artifacts have the expected filenames (`anygarden-0.8.0-*`, `anygarden_machine-0.8.0-*`, `anygarden_agent-0.8.0-*`). `dist/` cleaned afterwards.

## Decisions

- **0.8.0 (minor) over 0.7.2 (patch) and over 1.0.0 (major).** The rebrand breaks import paths and env-var names — that's an API contract change, so SemVer requires at minimum a minor bump. 1.0.0 was rejected because the project still has unstable interfaces beyond the rename (gateway/openhands/orchestrator strategies are all under active iteration); reserving 1.0 for a genuine API-stability milestone keeps SemVer signal honest.
- **All three packages bumped together** vs. independent versioning. Same reasoning as v0.7.0 and v0.7.1: the workspace ships as a coordinated set, internal cross-deps (`anygarden` → `anygarden-machine` for tests, etc.) get easier to reason about when versions line up. Diverging versions is a future-self problem we don't currently need.
- **No backward-compatibility shim.** Explicit user policy reaffirmed during the #394 brainstorm — no `import doorae` re-export, no `DOORAE_*` env-var fallback, no `~/.doorae/` → `~/.anygarden/` automatic migration. Reasoning: pre-stable project, small operator base that can update env vars manually, shim drift is a worse long-term tax than a one-time break. Could be revisited if external adoption picks up before 1.0.
- **0.7.1 PyPI artifacts left as-is, not yanked.** The wheels are technically misleading (claim to ship `anygarden` modules but actually ship `doorae` modules), but `anygarden-machine 0.7.1` never reached PyPI at all (issue #393), so the `anygarden` / `anygarden-agent` 0.7.1 wheels are the only operational reference point. Yanking them now would leave no anygarden-named release at all until 0.8.0 propagates. Better to let 0.7.1 stand as historical record and have 0.8.0 supersede it via normal version sort.
- **Commit body lists upgrade obligations explicitly.** Same content as the CHANGELOG, but mirrored in `git log` so reviewers and `git blame` walkers see the breaking-change scope without opening files.

## Result

- Three packages on `0.8.0`; uv resolution + builds clean.
- Three CHANGELOG entries describe the breaking changes for operators.
- Pending follow-up tracked separately:
  - Push tags `anygarden-v0.8.0`, `anygarden-machine-v0.8.0`, `anygarden-agent-v0.8.0` after PR merges, to trigger `.github/workflows/release.yml` and create GitHub Releases.
  - Manual `twine upload dist/*` after the release-workflow artifacts download (or `gh release download …` + upload). `anygarden-machine` PyPI upload remains blocked by issue #393's rate-limit cooldown — `anygarden` and `anygarden-agent` 0.8.0 should publish cleanly though, since those projects already exist on PyPI.
  - Operator release-notes / upgrade guide (separate work; CHANGELOG is the bare minimum, a dedicated `docs/UPGRADE.md` is still owed).
  - `drhub` / `drmachine` 0.7.0 PyPI yank — still gated on `anygarden-machine` publish.
