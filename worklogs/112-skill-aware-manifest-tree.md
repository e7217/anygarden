# feat(admin): skill-aware manifest tree with engine filter and script extensions (#112)

- Commit: `b99b4f3` (b99b4f37898261b578e7fd0ce68d43a49a410067)
- Author: Changyong Um
- Date: 2026-04-18T18:33:38+09:00
- PR: #112

## Situation

After #109 unified AGENTS.md into the file tree, `AgentEditDialog` still rendered the agent's manifest as a flat "group-by-top-level-prefix" list. That carried four concrete frictions: (1) claude-code admins saw `.codex/` / `.gemini/` / `.openhands/` groups that the engine will never read because agent `engine` is immutable post-creation; (2) a skill's files (`SKILL.md` + `scripts/helper.sh` + `references/api.md`) appeared as sibling entries instead of grouped under their skill, so structure was invisible; (3) only one level of grouping existed — Claude-Code-style nested skill directories had no UI; (4) the server's extension whitelist was text-only (`.md, .json, .toml, .txt, .yaml, .yml, .env`), so the `scripts/` subdirectory convention was unusable.

## Task

- Filter the tree per agent engine so only its CLI's config dir shows (plus the always-on `skills/`).
- Replace the flat group-by-prefix render with a recursive `TreeNode` model so arbitrary depth (`skills/<name>/scripts/<sub>/file.py`) renders as collapsible tree rows.
- Persist expand/collapse state per agent (localStorage) so admins don't re-open the same skill every session.
- Provide a "New skill" action that scaffolds `skills/<slug>/SKILL.md` with frontmatter plus a per-skill "+" quick-add button that prefills the "New file" form.
- Widen the server-side extension whitelist to include `.sh, .py, .js, .ts, .mjs` so skill `scripts/` is actually usable; keep the cluster/server and machine copies in lockstep.
- Keep upload/download (#98), AGENTS.md virtual entry (#109), and all existing admin flows (delete, edit, Save, overwrite-confirm) regression-free.

## Action

Backend:
- `packages/cluster/doorae/agent_files.py:27` — `_ALLOWED_EXTENSIONS` gains `.sh, .py, .js, .ts, .mjs` with a comment explaining why doorae itself is not an execution vector.
- `packages/machine/doorae_machine/agent_dir.py:44` — same five entries added to the machine-side validator so the materializer never rejects a path the server accepted.
- `packages/cluster/tests/test_agent_files_validation.py` — five new allowed paths (`skills/coder/scripts/*.{sh,py,js,ts,mjs}`); swapped the "rejected" case off the newly-allowed `.sh` / `.py` and onto `.bash`, `.pyc`, `.png`, `.zip`, `.so`, `.exe`.
- `packages/machine/tests/test_agent_dir.py` — mirrored the above changes.
- `packages/machine/tests/test_materialize.py:515` — flipped the pre-existing "rejects `.sh`" end-to-end materializer test to use `.bash` (an intentionally-omitted variant) so the guard still has teeth.

Frontend (all in `packages/cluster/frontend/src/components/AgentEditDialog.tsx`):
- Module-level additions: `ALLOWED_EXTENSIONS` gains the five script extensions; `ENGINE_PREFIXES` / `FALLBACK_ENGINE_PREFIXES` / `allowedPrefixesForEngine(engine)` encode the engine → admissible-prefix map (claude-code→`skills/` + `.claude/`, etc., with a `skills/`-only fallback for API-style engines).
- `TreeNode` discriminated union + `buildTree(files, allowedPrefixes)` pure function: one linear pass building nested dir nodes via a `dirByPath` map, followed by a recursive sort (dirs before files, alpha within each). Virtual rows short-circuit to the root; files outside the allowed-prefix set are silently dropped. Plus helpers `isSkillDirNode`, `countFilesRec`, `dirLabelFor`, `slugifySkillName`, `skillTemplate`, and the recursive `renderTreeNode`.
- New state: `expandedPaths: Set<string>`, `showNewSkillForm`, `newSkillName`. `loadInitial` no longer synthesizes prefix groups — it hands `files` to `buildTree(files, engineAllowedPrefixes)` via a `treeRoots` memo. An effect seeds `expandedPaths` from localStorage (or defaults to the engine's top-level prefixes) on open; `toggleExpanded` writes through to the same key (`doorae_agent_tree_<agent_id>`).
- New callbacks: `handleAddInSkill(skillName)` prefills the "New file" form with `skills/<name>/` and force-expands the skill dir; `handleCreateSkill` validates the slug, writes an in-memory dirty row with the frontmatter template, auto-expands and auto-selects; `handleCancelNewSkill` is the paired reset.
- `handleAddFile` prefix validation now runs against `allowedPrefixesForEngine(agent?.engine)` so claude-code agents get an immediate "path must start with one of: skills/, .claude/" error rather than a round-trip 400.
- JSX: the old `groupedFiles.map` block is replaced by a single `renderTreeNode` dispatch. A third "New skill" button (FolderPlus) joins Upload / New file in the Files section header; its form presents `skills/_____/SKILL.md` with inline prefix/suffix decorations and an autofocused name input. Chevron-left/right icons drive directory collapse; each `isSkillDirNode` dir renders a hover-revealed `+` quick-add button.
- `AgentEditDialog.test.tsx` — existing `.sh` extension-rejection case retargeted to `.bash`; existing download test expanded to click the `skills/greet` dir before selecting the file (nested tree requires the step); 16 new cases in four groups: `buildTree` unit tests (empty / virtual at root / nested dirs / prefix filter / deleted skip), `isSkillDirNode`, `slugifySkillName`, and UI-level checks for engine filter, skill quick-add, New skill happy path + rejection, and `.sh` admittance.

## Decisions

Rationale was pre-written in `.tmp/plan-112-skill-aware-manifest-tree.md`. Load-bearing threads:

- **Tree data model: recursive `TreeNode` vs flat groupedFiles + per-skill clustering.** Rejected flat+cluster because the acceptance criterion names a 4-deep path (`skills/foo/scripts/a/b.md`) explicitly — any depth-2 compromise would fail it. The codebase already has a precedent for path-splitting trees in `Sidebar.tsx:buildRoomTree`, so the recursive approach is not a new idiom here. Assumption to revisit: if a single agent's file count grows into the hundreds, the non-virtualized render will need attention.
- **Engine→prefix mapping in a frontend constant vs a server endpoint.** Chose a frontend `ENGINE_PREFIXES` map with a documented sync rule against `agent_files.py`. The engine list is short and stable, and a new endpoint would multiply coupling without reducing the real source-of-truth duplication (`_ALLOWED_PREFIXES` already lives in both `doorae/agent_files.py` and `doorae_machine/agent_dir.py`). Server-side `engine ↔ prefix` enforcement is deliberately out of scope — admin-only API means the UX filter is sufficient; any future tightening lands as a separate defense-in-depth change.
- **Flat extension whitelist vs per-prefix matrix.** Kept flat: a per-prefix matrix (e.g. `.claude/` → `.json/.toml/.yaml` only) would add a policy layer that the CLI ignores anyway, and a stray `.sh` under `.claude/` is admin-visible weirdness, not a security issue. The admin-only trust boundary makes "whitelist-policy clutter" the correct thing to optimize against.
- **Separate "New skill" button vs auto-detecting skill-shaped paths in the "New file" form.** Auto-detection is too implicit — admins would either miss the frontmatter template or be surprised when their `skills/foo/SKILL.md` new-file got unexpected content. The dedicated action makes the intent explicit and keeps the template a single well-understood shape.
- **`doorae_agent_tree_<agent_id>` per-agent expand state vs a global key.** Agents have wildly different skill layouts; global state would leak the previous agent's expansion into the next open. Storage cost is negligible (each entry is a tiny JSON array); cleanup-on-agent-delete is a separate follow-up worth doing only if storage growth becomes visible.
- **Skill quick-add "+" scoped to depth-2 `skills/<name>` only.** Nested dirs (`skills/<name>/scripts/`) don't get a "+" because the right mental model for "add inside a skill" is "top-level inside this skill" — further nesting is a manual path concern. This keeps the hover affordance from sprouting at every dir row and cluttering the tree.

Assumption to revisit: `engine` remains immutable post-creation. If a future feature lets admins switch an agent's engine, the filter would hide files from the previous engine rather than deleting them, and we'd need either a migration UI or a tolerance for "dead but visible" files.

## Result

- Claude-Code admins editing a manifest see only `skills/` + `.claude/`; codex admins see `skills/` + `.codex/`; API-only engines (`deep-agents`, `openai`, `anthropic`) see just `skills/`.
- Skill files now cluster under `skills/<name>/` as collapsible subtrees. A skill like `skills/greet/` with `SKILL.md` + `scripts/helper.sh` + `references/api.md` appears as a three-row tree with proper indentation and the ability to collapse any subdirectory.
- Expand state survives reloads per agent; default expand set on first open is the engine's top-level prefixes so content is visible without an extra click.
- "New skill" from "Code Review" → creates `skills/code-review/SKILL.md` with the `name: code-review` + `description: TODO` frontmatter and auto-selects it. Hover + on any skill dir prefills `skills/<name>/` for rapid file-in-skill workflows.
- `.sh`, `.py`, `.js`, `.ts`, `.mjs` under any whitelisted prefix now succeed on both the PUT path and the materializer; `.bash` / `.pyc` / `.png` remain rejected as negative-case tests assert.
- Test counts: cluster backend 413/413, machine 220/220 (swapped `.sh` rejection for `.bash`), frontend 199/199 (+16 new). `npm run build` and all three `uv run pytest` invocations pass.
- Out of scope, tracked for follow-ups: SKILL.md frontmatter parsing for inline description preview, token-count footer, expanded-state cleanup on agent delete, per-prefix script-allowance matrix, server-side engine ↔ prefix enforcement.
