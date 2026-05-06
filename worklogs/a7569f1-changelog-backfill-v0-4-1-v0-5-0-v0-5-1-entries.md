# docs(changelog): backfill v0.4.1 / v0.5.0 / v0.5.1 entries

- Commit: `a7569f1` (a7569f16558ccf5bb1637a9c9fec9dcad386946b)
- Author: Changyong Um
- Date: 2026-05-06T14:13:53+09:00
- PR: —

## Situation

PyPI has shipped `doorae-agent`, `doorae-cluster`, and `doorae-machine` at versions `0.4.1`, `0.5.0`, and `0.5.1` (all on 2026-04-28), but each per-package `CHANGELOG.md` jumped straight from a thin `## Unreleased` section to `## v0.4.0`. Anyone installing from PyPI had no record of what shipped between `v0.4.0` and the current `0.5.1`. GitHub Releases / tags also stopped at `v0.4.1`, but those gaps are addressed in follow-on PRs (release-workflow + retroactive tag/release creation); this PR closes only the documentation gap.

## Task

- Add `## v0.4.1`, `## v0.5.0`, `## v0.5.1` sections (with PyPI upload date `2026-04-28`) to all three package CHANGELOGs, between `## Unreleased` and `## v0.4.0`.
- Promote the prose currently sitting under `## Unreleased` in `agent` and `cluster` into `v0.4.1` — those entries (#280 collaboration_mode, #287 user-content augmentation, #289 mention-as-routing) all shipped in 0.4.1.
- Source new bullets from the actual merge commits on `main` between bump commits (`#263 → #299 → #303 → #305`), restricted per-package to commits that touched `packages/<pkg>/`.
- Leave a clean empty `## Unreleased` heading at the top of each file so the follow-on `0.6.0` PR has a landing spot.
- No code, no version, no dependency changes — pure docs.

## Action

For each of the three CHANGELOGs:

- Replaced the `## Unreleased` block (or inserted after the empty heading for `machine`) with three new dated sections.
- Bullets cite PR numbers as `[#XXX](https://github.com/e7217/doorae/pull/XXX)` matching the existing convention in `## v0.4.0`.

`packages/cluster/CHANGELOG.md`:
- `v0.5.1`: workspace bump alongside machine 0.5.1 (no functional change).
- `v0.5.0`: Windows-native `secure_chmod` consolidation in `app.py` + `bootstrap.py` (#301).
- `v0.4.1`: artifact pipeline (#296), ANSI fenced-code rendering (#291), tasks suite (#268, #272, #273, #276), per-agent `collaboration_mode` (#280, full safety-net prose preserved), self-MCP Streamable HTTP (#278), agent description for cross-agent recognition (#274), GPT-5.5 catalog (#267), `room_artifacts` migration rebase (#297), `AgentSettingsDialog` live agent prop (#282), dead-adapter cleanup (#294).

`packages/agent/CHANGELOG.md`:
- `v0.5.1`: workspace bump.
- `v0.5.0`: gemini_cli switches `killpg`/`SIGKILL` → `proc_kill.terminate_tree` (psutil), `psutil` dep added (#301).
- `v0.4.1`: collaboration mode wiring (#280, full prose preserved), centralize user-content augmentation (#287), centralize memory/roster injection (#295), mention-as-routing separation (#289, full prose preserved), opt-in collaborative synthesis (#283), GPT-5.5 (#267), dead-adapter cleanup (#294).

`packages/machine/CHANGELOG.md`:
- `v0.5.1`: `DELETE` + `FILE_DELETE_CHILD` rights on Windows `secure_chmod` (#305) — full root-cause prose from the commit body preserved (POSIX-mode-bit mapping omitted delete; `PROTECTED_DACL_SECURITY_INFORMATION` stripped inherited admin rights; second-spawn manifest prune failed).
- `v0.5.0`: safefs Windows backend (`CreateFileW` + `FILE_FLAG_OPEN_REPARSE_POINT` via ctypes, no `pywin32` dep), `SetNamedSecurityInfoW` + `PROTECTED_DACL`, `proc_kill.terminate_tree`, `subprocess_group_kwargs()` (#301).
- `v0.4.1`: machine-side artifact pipeline (#296), `workspace/memory/outbox` → canonical outbox bridge (#298), dead-adapter cleanup (#294).

Diff: `+156 / -1` lines across the three CHANGELOGs.

## Decisions

Approach options weighed:

- **A. Single consolidated note** — collapse 0.4.0 → 0.5.1 into one "post-0.4.0 cumulative" entry. Rejected: erases the PyPI version boundaries that users actually pin against; if someone reports a regression on 0.5.0 we want to be able to point at exactly which PRs that version contained.
- **B. Per-version per-package backfill (chosen)** — each PyPI release gets its own dated section in the package whose code changed. Matches the existing CHANGELOG style (`## v0.4.0`, `## v0.3.x`, …) and lets the follow-on retroactive `0.5.0` / `0.5.1` GitHub Releases link directly to the matching CHANGELOG anchor.
- **C. Auto-generate from `git log`** — tooling-driven (e.g. `release-please` style). Rejected for this PR: out of scope, would require a separate config/tooling decision; the manual pass also doubles as an audit of what *actually* shipped.

What tipped toward B: existing CHANGELOGs already follow per-version sections with PR-linked bullets and topic-grouped subsections (`### Features — …`, `### Fixes`); matching that pattern keeps the file readable and lets a reader diff "what's in 0.5.0" by reading one section.

Per-package routing: every commit between bump points was inspected with `git show --stat` and a `packages/(agent|cluster|machine)` filter. A commit touches only the packages whose directory it modified — e.g. `feat(rooms)` commits go in `cluster` (rooms is a cluster + frontend concern, not agent/machine), `feat(agents)` commits split based on which directories were actually touched. The `0.5.1` bump (`#305`) was machine-only in code, so `agent` and `cluster` get a "workspace bump" stub rather than fabricated content — workspace lockstep is the project's release convention but the user-visible delta is zero.

`v0.4.1` content for `agent` and `cluster` reuses the prior `## Unreleased` prose verbatim (those PRs landed in 0.4.1) rather than re-summarising — preserves the original author's voice and detail level (e.g. the per-room `PeerHandoffBudget` / `MAX_PEER_DEPTH` / `MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN` numbers in #280, the `<room_conversation>` XML wrapping rationale in #284). Re-summarising would have lost those concrete thresholds.

Date `2026-04-28` for all three versions: PyPI `upload_time` for every package × every version falls on 2026-04-28 (verified via `https://pypi.org/pypi/<pkg>/json`). Using a single date keeps the CHANGELOG consistent with the manual `make release-*` Makefile flow that produced these artifacts (no automation, all uploaded same day).

Assumptions, if violated → revisit:
- New empty `## Unreleased` heading is preserved at the top so the next release PR can fill it. If the project switches to release-please / similar automation, that empty heading may need a different shape.
- `0.5.1` is genuinely a no-op for `agent` and `cluster`. If review surfaces a regression that 0.5.1 fixed in those packages, the `Workspace bump` stub needs a real entry.

## Result

- `packages/cluster/CHANGELOG.md`, `packages/agent/CHANGELOG.md`, `packages/machine/CHANGELOG.md` now document all PyPI releases through `0.5.1` with PR-linked bullets.
- Empty `## Unreleased` heading present at the top of each file, ready for the `0.6.0` bump PR (PR ② of this release-cleanup track).
- No code, dependency, or version changes — CI should pass on docs-only diff.
- Pending: PR ② (`pyproject` 0.5.1 → 0.6.0 + `## v0.6.0` section), PR ③ (`.github/workflows/release.yml` for tag-triggered GitHub Releases + dist artifact attach), then retroactive `0.5.0` / `0.5.1` tags + Releases pointing at this CHANGELOG content, and a fresh `0.6.0` tag/Release once ② lands.
