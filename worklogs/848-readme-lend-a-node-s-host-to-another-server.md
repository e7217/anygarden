# docs(readme): lend a node's host to another server (#848)

- Commit: on `docs/848-readme` (squashed in the PR)
- Author: Changyong Um (with Claude)
- Date: 2026-10-15
- PR: follow-up to #849

## Situation

#849 (merged as 42fc7fd) shipped machine links: a host that runs `anygarden start` can also be a machine of another server. It also added `--data-dir` for the machine daemon. The runbook, CHANGELOG and DESIGN.md were updated, but neither README mentioned the feature.

## Task

Document the user-facing flow where readers find machine setup: the root README's Quick Start, next to "Add more machines".

## Action

- `README.md`: new "Lend a node's host to another server" subsection.
  - The two web UI steps (copy the code on the other server, paste it on this node).
  - What the node does with the connection: dials out, persists across restarts, stops agents on disconnect.
  - The ignored Update button.
  - The `--data-dir` terminal path, and a link to the runbook section.
- `packages/cluster/README.md`: one CLI example line for `machine run --data-dir`.

## Decisions

N/A — documentation of the merged behavior. The detailed reference stays in the runbook; the README only gives the entry point.

## Result

The README now covers the feature. Docs only; no code or tests changed.
