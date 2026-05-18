# chore(release): rename PyPI distributions to dr-prefixed names

- Commit: `7c8ee58` (7c8ee58fa20861ed4c3ddf0ccbc9b82e1c4daa07)
- Author: Changyong Um
- Date: 2026-05-19T01:04:20+09:00
- PR: —

## Situation

The three Python distributions in the monorepo were published to PyPI as `doorae-cluster`, `doorae-machine`, and `doorae-agent` — verbose, and `doorae-cluster` is misleading because the package is the central chat hub, not a clustering library. A shorter, brand-aligned naming was wanted so users can `pip install` faster and the names match the eventual unified `dr` CLI surface. Nothing about the existing Python code, CLI commands, or source layout had to change — the rename is purely a distribution-name (`[project] name`) concern.

## Task

- Rename the PyPI distributions only (not Python module paths, CLI entry points, source directories, or `doorae` brand).
- Keep `packages/cluster/`, `packages/machine/`, `packages/agent/` directory names intact (renaming dirs would ripple into hundreds of references).
- Update the release workflow so a new tag scheme triggers builds against the correct source directory.
- Preserve CHANGELOG and worklog history (those describe past releases under the old names — rewriting them would falsify the record).
- Avoid PyPI namespace collisions.

## Action

- `pyproject.toml` (root, `:18-21`): `[tool.uv.sources]` keys renamed (`doorae-agent` → `dragent`, `doorae-cluster` → `drhub`, `doorae-machine` → `drmachine`).
- `packages/agent/pyproject.toml:6,40`: `name = "dragent"`; backward-compat install hint updated.
- `packages/cluster/pyproject.toml:6,36,56,66`: `name = "drhub"`; dev dep string, transitive-via comment, and `[tool.uv.sources]` key all updated.
- `packages/machine/pyproject.toml:6`: `name = "drmachine"`.
- `.github/workflows/release.yml`: tag patterns switched to `drhub-v*`/`drmachine-v*`/`dragent-v*`; parser strips `dr` (no hyphen) and maps `hub` → `cluster` directory via a case statement; previous-tag lookup and release title use the new prefix.
- `Makefile:26-33`: release target comments updated; `release-cluster` annotated with the `drhub → packages/cluster/` mapping.
- `README.md:9-11,27`: package table and `dragent` text reference updated.
- `packages/agent/README.md:1-17`: title and `pip install` lines.
- `packages/machine/README.md:1-3`: title and `drhub server` reference in the intro.

CLI commands (`doorae-server`/`doorae-machine`/`doorae-agent`), Python import paths (`doorae`, `doorae_agent`, `doorae_machine`), `packages/cluster/doorae/static/` mount, frontend code, and DB tables/endpoints are deliberately untouched.

## Decisions

The naming went through several iterations during design discussion:

- **`dr-core` / `dr-server` / `dr-hub` for the cluster package**: settled on **`hub`** because `core` reads as "internal library" and `server` collides with the machine daemon (also a long-running server). `hub` uniquely captures the cluster's role as the WebSocket/REST meeting point and keeps `hub`/`machine`/`agent` at the same noun layer.
- **`drnode` vs `drmachine`**: rejected `drnode` because the entire codebase (CLI, modules, API endpoints, UI strings, DB) is built around the "machine" concept. PyPI name `drnode` with imports from `doorae_machine` would create a confusing mismatch, and renaming the concept itself is a separate, much larger refactor. Also `node` is overloaded (Node.js, K8s node).
- **Hyphenated `dr-agent` vs hyphenless `dragent`**: PyPI check found `dr-agent` is already owned by an unrelated `dr-agent-lib` deep-research library (versions 0.1.0–0.1.2). Hyphenless form sidesteps the collision entirely (PyPI normalizes `dr-hub` and `dr_hub` to the same name, but `drhub` is a distinct namespace), keeps cross-package consistency, and visually matches the unified-CLI direction (`dr` program + one-word subcommand). The minor gain in two-word readability from the hyphenated form was not worth introducing one hyphenless outlier just for `dragent`.
- **Directory rename `packages/cluster/` → `packages/hub/`**: rejected. The directory is referenced across the Makefile, frontend tooling, docs, scripts, and the workflow's `working-directory`. Mapping `drhub` → `cluster` inside the workflow parser is a one-line escape hatch that contains the impact.

Assumption worth revisiting: if a future unified `dr` CLI lands and the project decides to also rename the `doorae` Python module / CLI commands to `dr*`, the workflow's `hub → cluster` directory mapping should be revisited alongside a directory rename.

Alternatives explicitly weighed and rejected: `dr-sdk` (drops the "agent" keyword), `dr-agent-sdk` (too long), `doorae-hub`/`doorae-machine`/`doorae-agent` (minimal change but forfeits the `dr` brand).

## Result

- `uv sync --all-packages` resolves cleanly under the new names: workspace shows `dragent==0.6.0`, `drhub==0.6.0`, `drmachine==0.6.0`.
- `uv build` in each package produces artifacts with the expected wheel names (`dragent-0.6.0-py3-none-any.whl`, `drhub-0.6.0-py3-none-any.whl`, `drmachine-0.6.0-py3-none-any.whl`).
- No code-level changes — Python imports, CLI invocations, and tests are unaffected.
- Pending external steps (not in this commit): PR/merge, first tag push (`drhub-v0.6.0` etc.) to trigger the release workflow, and optional deprecation notes on the old `doorae-cluster`/`doorae-machine`/`doorae-agent` PyPI projects pointing users to the new names.
