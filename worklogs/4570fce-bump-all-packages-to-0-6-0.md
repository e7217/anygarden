# chore(release): bump all packages to 0.6.0

- Commit: `4570fce` (4570fce6b0e4e527738885968ab0d8f6498289c3)
- Author: Changyong Um
- Date: 2026-05-06T14:20:12+09:00
- PR: —

## Situation

Three packages on PyPI sit at `0.5.1` (uploaded 2026-04-28), but `main` has accumulated a substantial wave of post-0.5.1 work — Goal scheduler / autonomous-responsibility MVP, 3-tier per-agent permission model, task auto-routing, and the right rail polish series — none of which has been released. The pyproject `version` was still `0.5.1`, so `make release-*` wouldn't even produce a new artifact. PR #340 (CHANGELOG backfill) is the sibling cleanup that fills the docs gap; this PR is the version cut.

## Task

- Bump `version` in `packages/{agent,cluster,machine}/pyproject.toml` from `0.5.1` → `0.6.0` in lockstep (matches the existing release cadence — all three packages bump together).
- Add a `## v0.6.0 (2026-05-06)` section under `## Unreleased` in each package CHANGELOG, sourced from the `main` commits between `0.5.1` (`76a6ece` / `#305`) and current HEAD (`13a323e`), routed per package by which directories the commit touched.
- Hold workspace consistency: even when an individual package has no functional changes (none qualifies here, but the precedent from `0.5.1`'s machine-only fix matters), keep all three on the same minor.

Constraints:
- No code changes — pure `pyproject` + CHANGELOG.
- No `uv.lock` change (verified prior bump `da75da1` only touched the three pyproject files; doorae versions don't appear in any `uv.lock`).
- Branch off PR #340 (`chore/release-changelog-backfill`) so the v0.6.0 section sits cleanly above the freshly-backfilled v0.5.1.

## Action

`packages/agent/pyproject.toml`, `packages/cluster/pyproject.toml`, `packages/machine/pyproject.toml`: line 7 `version = "0.5.1"` → `"0.6.0"`.

`packages/cluster/CHANGELOG.md`: inserted `## v0.6.0 (2026-05-06)` between `## Unreleased` and the backfilled `## v0.5.1`. Sections:
- `### Features — autonomous responsibility & Goals UI (#302)` — #306 (Phase 1 right context rail), #307 (Phase 2 Goal scheduler + executor), #308 (Phase 3 Goals UI).
- `### Features — per-agent permission level (#309)` — #310 (PR-A 3-tier + codex sandbox dial), #311 (PR-B gemini + claude-code mappings + topology ⚠ + activity).
- `### Features — task auto-routing` — #315 (auto-rep invariant + assignee picker), #316 (batch auto-route via room representative).
- `### Features — right rail polish (#329)` — #324 (density polish), #330 (viewport-driven default, Phase 1), #331 (agent message + file-chip widths, Phase 2), #332 (RoomHeader absorption, Phase 3), #333 (sm-breakpoint search hide + menu fallback, Phase 4).
- `### Features — tasks UI` — #322 (TasksPanel sections + terminal cleanup).
- `### Fixes` — #321 (cluster MCP exposure + status enum/UI parity), #318 (migration renumber 038 → 039), #317 (scheduler task broadcast + stuck task sweeper), #326 (right rail right-edge alignment), #328 (hover text truncation), #335 (task row overflow), #337 (substrate viewport overflow), #339 (task pickup timeout + status directive).

`packages/agent/CHANGELOG.md`: inserted `## v0.6.0`. Two subsections — `Features — per-agent permission level (#309)` (#310 wires 3-tier into agent runtime), `Fixes` (#321 cluster MCP exposure + status parity).

`packages/machine/CHANGELOG.md`: inserted `## v0.6.0`. One subsection — `Features — per-agent permission level (#309)` (#310 wires permission model into spawner / engine launch, #311 gemini + claude-code permission mappings + codex sandbox dial).

Diff: 6 files, +88 / -3.

## Decisions

Approach options weighed:

- **A. Patch bump 0.5.1 → 0.5.2** — accumulate features under continuing 0.5.x. Rejected: the autonomous-responsibility Goal scheduler (#307) and the 3-tier permission model (#310) are user-visible new capabilities, not bugfixes; PyPI users on `^0.5.0` would silently get them under semver-loose pinning. Cuts against the existing convention used at `da75da1` ("Bumping minor (not patch) because Windows native execution is a new supported platform — meaningful capability expansion, not a bugfix").
- **B. Minor bump 0.5.1 → 0.6.0 (chosen)** — semver-correct for the diff content, lets PyPI users opt in via `^0.6.0` pinning, and creates a clean tag/release boundary for the post-Windows-native cycle.
- **C. Per-package independent versioning** — bump only the packages that actually changed (e.g. cluster + machine + agent each at different versions). Rejected: breaks the "they all go together" cadence the CHANGELOGs explicitly call out (`v0.3.0`/`v0.3.1`/`v0.3.2` machine entries: "version bumped to keep the three monorepo packages aligned"). Workspace lockstep simplifies `uv sync --all-packages` and downstream consumers who install the trio together.

What tipped toward B: the existing `0.5.0` bump set the precedent of "minor bump for capability expansion even when only one package changed substantially," and the current cycle has substantially more capability expansion across more packages than `0.5.0` did.

Per-package routing methodology: for each commit between `76a6ece` (0.5.1 bump) and current HEAD, ran `git show --stat` and filtered for `packages/(agent|cluster|machine)`. A commit's bullet appears in the CHANGELOG of every package it touched. Cross-cutting commits — `#310` (all three), `#311` (cluster + machine) — appear in the relevant CHANGELOGs only, with consistent wording across each. The "rooms" / "tasks" / "frontend" commits all stayed inside `packages/cluster/` (frontend lives there), so cluster carries the vast majority of bullets.

Date `2026-05-06` is today's date — the cut is happening now, so the CHANGELOG header reflects the release-decision date even though PyPI upload (via `make release-*`) and tag/Release creation happen out-of-band post-merge.

Branched off `chore/release-changelog-backfill` (PR #340) rather than `main`: keeps the v0.6.0 section landing above the backfilled v0.5.1 section in a single coherent file. Alternative was branching off `main` and accepting that v0.6.0 would land above the still-stale `## Unreleased` content (with #340 merging separately and the two CHANGELOGs racing on the same heading lines). Chose the chained-branch approach to keep diffs reviewable and ordering deterministic; if #340 merges first the rebase here is a no-op, and if this PR somehow merges first GitHub will surface the conflict on #340's `## Unreleased` line.

Assumptions, if violated → revisit:
- All `0.5.1 → HEAD` commits are intended for this release. If something on `main` should be held back (e.g. an experimental feature behind an unfinished flag), it would need to be either reverted on `main` or excluded explicitly.
- `uv.lock` files don't carry doorae version pins (verified empirically by `grep "version = \"0\\." uv.lock | grep doorae` returning empty). If a future workspace config adds an explicit pin, the lockfile would need to bump alongside.

## Result

- All three packages now declare `version = "0.6.0"` in their pyproject.toml; cleared for `make release-{agent,cluster,machine}` to produce a new PyPI artifact.
- Each CHANGELOG carries a `## v0.6.0 (2026-05-06)` section with PR-linked bullets, organized by topic (autonomous responsibility, permission level, task routing, right rail polish, tasks UI, fixes).
- New empty `## Unreleased` heading preserved at the top of each file for the next cycle.
- Pending: PR #340 (CHANGELOG backfill) to land first, then this PR; PR ③ (`.github/workflows/release.yml` for tag-triggered Releases + dist artifact attach); retroactive `0.5.0` / `0.5.1` GitHub Releases (PyPI artifacts already exist); `0.6.0` tag + Release after this PR merges and `make release-*` runs.
