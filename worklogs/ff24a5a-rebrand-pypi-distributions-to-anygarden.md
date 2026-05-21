# chore(release): rebrand PyPI distributions to anygarden

- Commit: `ff24a5a`
- Author: Changyong Um
- Date: 2026-05-21
- PR: —

## Situation

The dr-prefix PyPI rebrand from #387 (drhub / drmachine / dragent) was a half success. `drhub-0.7.0` and `drmachine-0.7.0` published cleanly via `twine upload`, but `dragent-0.7.0` was rejected by PyPI with `400 The name 'dragent' is too similar to an existing project` — collision with the unrelated `dr-agent` deep-research library. Choosing `dragent` had been intended *specifically* to sidestep that collision (per #387 commit body); PyPI's similarity check is broader than the name owner's heuristic and treats `dragent`/`dr-agent` as duplicates after normalization. With the third package stuck and the brand still in flux, the team picked a new service name — **anygarden** — and decided to migrate all three distributions one more time rather than ship one package half-renamed.

## Task

- Replace all three PyPI distribution names: `drhub` → `anygarden`, `drmachine` → `anygarden-machine`, `dragent` → `anygarden-agent`. Single brand prefix, hyphen-separated, no clever abbreviations that PyPI's similarity check might flag.
- Bump every package to `0.7.1` because `0.7.0` is already on PyPI for two of the three distributions under the old names. Same code as v0.7.0 — this is a release-infrastructure-only delta.
- Rewrite `.github/workflows/release.yml` tag parser for the new prefix layout (`anygarden-v*`, `anygarden-machine-v*`, `anygarden-agent-v*`) and translate hyphens → underscores in the dist-file glob (uv emits `anygarden_machine-0.7.1-*.whl` per PEP 491/625).
- Keep Python import paths, CLI commands, source directories, and CHANGELOG history untouched — only PyPI metadata + tag scheme moves. Mirrors the policy from #387 to keep the migration cost low.
- Update README, Makefile release targets, and root `[tool.uv.sources]` so the workspace install stays consistent.

## Action

- `packages/cluster/pyproject.toml` — `name = "anygarden"`, `version = "0.7.1"`; dev-dep ref `drmachine` → `anygarden-machine`; `[tool.uv.sources]` ref updated.
- `packages/machine/pyproject.toml` — `name = "anygarden-machine"`, `version = "0.7.1"`.
- `packages/agent/pyproject.toml` — `name = "anygarden-agent"`, `version = "0.7.1"`; comment example `pip install dragent[...]` → `pip install anygarden-agent[...]`.
- `pyproject.toml` (root) — `[tool.uv.sources]` map: `anygarden`, `anygarden-agent`, `anygarden-machine` all `{ workspace = true }`.
- `.github/workflows/release.yml` rewritten:
  - Tag triggers: `anygarden-v*` / `anygarden-machine-v*` / `anygarden-agent-v*`.
  - Parser uses `DIST_NAME="${TAG%-v*}"`, explicit case mapping to `packages/<pkg>` directories (cluster/machine/agent).
  - Release upload glob uses `FILE_PREFIX="${DIST_NAME//-/_}"` to bridge PyPI dist name → uv-emitted filename (hyphen-to-underscore translation per PEP 491/625).
- `Makefile` — `release-{agent,machine,cluster}` targets switched to `uv build --package <dist-name>` from repo root, upload globs match the underscore-normalized filenames.
- `README.md` Packages table now shows the anygarden distributions; the `dragent`-caching footnote updated to `anygarden-agent`.
- `packages/{cluster,machine,agent}/CHANGELOG.md` — new `## v0.7.1 (2026-05-21)` Release-infrastructure entries documenting the rebrand. The agent CHANGELOG also notes that `dragent 0.7.0` never reached PyPI so v0.7.1 is the first anygarden-agent release.

Verification: `uv build --package anygarden`, `--package anygarden-machine`, `--package anygarden-agent` all succeed locally with the expected filenames (`anygarden-0.7.1-*`, `anygarden_machine-0.7.1-*`, `anygarden_agent-0.7.1-*`). Full cluster test suite — 993 passed, 1 deselected.

## Decisions

- **`anygarden-` prefix over `ag-` short prefix.** Two pragmatic reasons:
  1. `ag` is already taken on PyPI (HTTP 200), so a single short name for the cluster wasn't even available — the symmetry argument for `ag-` evaporates.
  2. Short, two-letter prefixes are exactly the kind of pattern PyPI's similarity check rejects (`agt`, `agi`, etc. would all be at risk). After the `dragent` incident the team explicitly accepted some verbosity in exchange for predictable acceptance.
- **Option B layout (`anygarden` / `anygarden-machine` / `anygarden-agent`) over A/C/D.** Functional names + brand prefix mirrors how `langchain-*`, `pytorch-*`, etc. structure their package families. A user who sees `anygarden-machine` immediately knows it's the machine daemon — no metaphor decoding required. Option A (`anygarden-plot` / `anygarden-seed`) and C (`anygarden-gardener`) keep the garden metaphor tighter but lose discoverability; rejected to keep onboarding friction low.
- **Shallow rename (PyPI metadata only) over deep rebrand (import paths, CLI commands, env vars, directories).** A deep rebrand would touch hundreds of files plus user-visible state (`DOORAE_*` env vars, `~/.doorae/` paths). #387 established the policy of keeping `doorae` import paths stable through PyPI renames; staying consistent with that minimises the surface this PR has to defend in review. Deep rebrand is explicitly deferred — not abandoned.
- **Bump to 0.7.1 instead of 0.8.0.** The diff is metadata only; no behaviour or API changes since v0.7.0. Calling it a minor bump would mislead release-notes scanners into expecting features. The patch number is the honest signal: *new PyPI name, same code*.
- **Hyphen→underscore translation in the workflow glob via `${DIST_NAME//-/_}`** rather than special-casing per package. Keeps the workflow data-driven and future-proof: if a fourth package lands later, the parser handles it as long as it follows the `anygarden-<x>` convention.
- **Don't yank drhub / drmachine in this PR.** PyPI yank happens after the new releases publish so there's never a window where neither name works. Tracked as a follow-up task.

## Result

- All three packages now identify as `anygarden` / `anygarden-machine` / `anygarden-agent` at 0.7.1; `uv sync --all-packages` resolves the new names; 993 cluster tests pass; local `uv build --package <name>` produces correctly-named artifacts for each.
- Release workflow restructured to accept the new tag scheme with PEP 491/625-aware artifact globbing.
- Pending (tracked separately): merge PR → push `anygarden-v0.7.1` / `anygarden-machine-v0.7.1` / `anygarden-agent-v0.7.1` → verify three GitHub Releases auto-created → `twine upload` to PyPI → yank `drhub-0.7.0` and `drmachine-0.7.0` on PyPI with a deprecation pointer to the anygarden line.
- The `dragent` PyPI namespace will be left empty (never published) so it does not block future renames; the migration history of the `doorae-*` → `drhub`/`drmachine` distributions stays visible in CHANGELOG for archaeology.
