# fix(agents): always write AGENTS.md so agents without instructions get platform guidance (#857)

- Commit: `7ae1e0d` (7ae1e0de0d72be0e8f165c51b0472bc725effe06)
- Author: Changyong Um
- Date: 2026-10-16T14:51:50+09:00
- PR: — (issue #857)

## Situation

`Spawner._materialize_agent_dir` only wrote `AGENTS.md` when `msg.agents_md` was non-`None`. The agent create dialog leaves `agents_md` absent when the instructions field is empty, so such agents never received the platform-composed sections of `AGENTS.md`: room memory paths, shared files, outbox/reply attachment conventions (#854), available skills, and delegation guidance. A live check confirmed the regression: a codex agent without instructions wrote its chart to `workspace/outputs/` and attached nothing to its reply; the same agent attached both files correctly once `AGENTS.md` was present.

## Task

- Always write `AGENTS.md`, even when `msg.agents_md is None`.
- When there is no admin body, omit it from the file rather than emitting an empty leading block.
- Ensure `CLAUDE.md` symlink and the codex workspace `AGENTS.md`/`CLAUDE.md` symlinks always point at the file (no dead symlinks).
- Update tests to match the new invariant: agents without instructions still get platform guidance.

## Action

**`packages/cluster/anygarden/machine/spawner.py`**

- `_compose_agents_md` (line 277): changed `sections = [base.rstrip()]` to `sections = [base] if base else []`, so an empty admin body contributes no blank lines. Added `.lstrip("\n")` to the final `"\n".join(sections)` return (line 407) to clean leading newlines when sections starts with platform content rather than admin text.
- `materialize_agent_dir` (line 664–665): removed the `if msg.agents_md is not None:` guard; `AGENTS.md` is now written unconditionally.
- Symlink block (line 725–733): removed the `if msg.agents_md is not None:` guard around `CLAUDE.md` symlink creation; comment updated to note the link is never dead.
- Codex workspace symlink block (line 817–826): removed `composed = ... if ... else None` conditional and the `if composed is not None:` guard; `AGENTS.md` and `CLAUDE.md` workspace slots are now linked unconditionally.

**`packages/cluster/tests/machine/test_materialize.py`**

- Replaced `test_agents_md_absent_when_no_agents_md` with `test_agents_md_carries_platform_guidance_without_agents_md`: asserts `AGENTS.md` exists and starts with `## Memory` and contains the outbox/shared path hints.
- Replaced `test_claude_md_absent_when_no_agents_md` with `test_codex_workspace_links_agents_md_without_agents_md`: asserts the codex workspace symlink exists and its target contains outbox paths.
- Replaced `test_claude_md_removed_when_agents_md_cleared` with `test_claude_md_links_agents_md_without_agents_md`: asserts `CLAUDE.md` is a symlink to `AGENTS.md` even when `agents_md=None`.
- Replaced `test_agents_md_removed_when_agents_md_cleared` with `test_agents_md_drops_body_when_agents_md_cleared`: asserts the admin body is gone but the file still starts with `## Memory` after clearing.
- Updated `TestMaterializePrune` stale-symlink test: asserts `AGENTS.md` persists (platform-only) and `CLAUDE.md` remains a valid symlink after re-materialize with `agents_md=None`.

## Decisions

- **Always write vs. conditional write**: the conditional guard was the original design intent — `AGENTS.md` was purely an admin instructions file. The platform sections (memory, shared files, outbox since #290) already lived in this file, so instruction-less agents silently lost them; #854 made the outbox the path to reply attachments, which surfaced the gap. Always writing it was the minimal, least-surprising fix: no new file, no schema change.
- **Empty admin body → omit from output vs. keep blank line**: a blank leading line in a Markdown file creates visual noise and made tests fragile. Dropping empty sections with `if base else []` is the cleaner invariant: the file is non-empty iff there is at least one platform section.
- **Alternative considered — inject platform sections server-side only for agents without instructions**: rejected because it would have required a separate compose path and split the logic into two functions, increasing maintenance surface.
- **Assumption**: if a future change makes platform sections conditional themselves, the `sections` list could become empty for agents with no instructions and no platform features — at that point the "always write" invariant should be revisited.

## Result

- Agents created without an instructions body now receive the full platform guidance in `AGENTS.md`, enabling correct outbox attachment, shared-file awareness, and skill discovery.
- `CLAUDE.md` and the codex workspace symlinks are always valid; no dead symlinks.
- Five tests updated to assert the new invariant; four old tests (asserting absence of the file) removed.
- No API or manifest schema changes.
- Verification: cluster pytest 3835 passed / 7 skipped. Live check on an isolated node from this branch (codex-cli 0.162.1, `trusted` because this VM's AppArmor userns restriction breaks the standard sandbox, see #858): an agent created with `agents_md = None` got an AGENTS.md starting with `## Memory`, and on its first request it attached `quarterly-sales.svg` and `quarterly-sales-report.md` to the reply.
