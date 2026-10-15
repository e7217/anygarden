# docs: bring the READMEs up to the 0.19 single distribution (#837)

- Commit: `f5af771` (f5af7710c06fc78d045069ca6e7ac245f9d782f1)
- Author: Changyong Um
- Date: 2026-10-15T10:14:21+09:00
- PR: #837

## Situation

Version 0.19 consolidated `anygarden-machine` and `anygarden-agent` into a
single `anygarden` distribution with optional extras (`[machine]`, `[agent]`).
The TypeScript agent runtime was removed in #746, and the agent runtime was
shipped inside the `anygarden` package in #832. The project READMEs still
described the old multi-distribution layout and referenced the retired
TypeScript client alongside outdated engine examples (`claude-code`) and the
older development workflow (`pip install -e ".[dev]"` + bare `pytest`).

## Task

- Remove references to the retired TypeScript agent runtime from `README.md`.
- Clarify when Node.js is actually required (UI build from checkout, and Pi engine hosts — not for the PyPI server install).
- Add an "Upgrading from 0.18" section covering: Python 3.12 requirement, removing retired distributions, agent interpreter coupling, and renamed commands.
- Update `packages/cluster/README.md`: replace the retired `claude-code` engine example with `codex-cli`, and document the `uv`-based test run.

## Action

**`README.md`**
- `README.md:28-30`: Rewrote the Node.js prerequisite note — removed mention of the npm-distributed TypeScript runtime; clarified that Node.js is needed to build the UI from a checkout and on Pi engine hosts.
- `README.md:34-36`: Dropped the sentence about the TypeScript client not providing engine adapters; simplified to remove stale reference.
- `README.md:113-128`: Expanded the retirement notice into a full "Upgrading from 0.18" subsection documenting Python 3.12 requirement, how to remove retired distributions (pip and uv tool), the new interpreter coupling, and the renamed `anygarden agent` / `anygarden client` commands.

**`packages/cluster/README.md`**
- `packages/cluster/README.md:20-21`: Tightened the deprecation note for `anygarden-server` (dropped "for one release" which was no longer accurate).
- `packages/cluster/README.md:30`: Replaced `--engine claude-code` with `--engine codex-cli` in the quick-start example to match the current supported engine.
- `packages/cluster/README.md:77-86`: Replaced the old `pip install -e ".[dev]" && pytest` development snippet with the current `make setup` + `uv run pytest -n logical` workflow; added note that Pi harness tests require Node.js on PATH.

## Decisions

- **Why update both READMEs in one commit**: The changes are tightly coupled — the cluster README's engine example, the root README's Node.js note, and the upgrade guide all describe the same 0.18→0.19 transition. Splitting them would leave the docs transiently inconsistent.
- **Why add "Upgrading from 0.18" as a subsection rather than a separate doc**: The audience for the README is a first-time installer or someone upgrading; a subsection in the main README is the most discoverable location without adding another file to maintain.
- **Why prefer `codex-cli` over other engine names**: The commit body cites the replacement directly; `claude-code` was the retired engine and `codex-cli` is its live successor at this point in the codebase.
- **Assumption**: Once 0.19 is the stable release and 0.18 is fully unsupported, the "Upgrading from 0.18" section can be dropped or archived.

## Result

Both READMEs now accurately reflect the 0.19 single-distribution layout. Users
upgrading from 0.18 have a dedicated section covering the three most common
failure modes (wrong Python version, stale retired packages, renamed commands).
The development workflow in `packages/cluster/README.md` matches the actual
`uv`-based toolchain. No behavior changes; docs only.
