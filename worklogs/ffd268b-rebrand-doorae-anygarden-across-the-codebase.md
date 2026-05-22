# chore: rebrand doorae → anygarden across the codebase

- Commit: `ffd268b`
- Author: Changyong Um
- Date: 2026-05-22T19:47:28+09:00
- PR: —

## Situation

The project shipped under the **doorae** name across PyPI distributions, Python imports, CLI commands, environment variables, data directories, frontend UI strings, and documentation. The PyPI distribution names had already been rebranded twice (`doorae-*` → `dr*` in #387, then `dr*` → `anygarden*` in #392), but the rest of the codebase still said *doorae* everywhere — leaving the brand half-applied. The team decided to drop the doorae name entirely and migrate everything to **anygarden** in a single bulk operation, with no backward-compatibility shims or migration paths.

## Task

- Replace every reference to *doorae* (and its case variants `Doorae`, `DOORAE`, `DoorAE`) with **anygarden** across the entire repository, excluding history-preservation directories.
- Cover six distinct surface areas in one commit so the codebase never spends a build in a half-renamed state: source directory names, Python import paths, CLI entry points, environment variable prefixes, on-disk data paths, frontend strings/storage keys, the npm package, and documentation.
- Preserve git rename history wherever possible (`git mv` for source directories).
- Keep `worklogs/` and `docs/history/` untouched — those are historical records and must reflect the names used at the time they were written.
- Ensure the workspace still builds and all per-package test suites pass after the substitution.

## Action

Directory renames via `git mv`:
- `packages/cluster/doorae/` → `packages/cluster/anygarden/`
- `packages/agent/doorae_agent/` → `packages/agent/anygarden_agent/`
- `packages/machine/doorae_machine/` → `packages/machine/anygarden_machine/`

Four-pass Perl substitution on tracked files (excluding `worklogs/` and `docs/history/`):
1. Strict word-boundary pass: `\bdoorae_agent\b`, `\bdoorae_machine\b`, `\bdoorae-server\b` (and other kebab CLI names), `\bDOORAE_\b`, `Doorae`, `\bdoorae\b`.
2. Snake-prefix pass: `\bdoorae_` and `\bdoorae/` and `\bdoorae\.db\b` to catch identifiers like `doorae_token`, paths like `doorae/db/migrations`, and the SQLite filename.
3. Expanded-glob pass: same regexes but with `'**/Makefile'`, `'*.ini'`, and `CLAUDE.md` (untracked but workspace-authoritative) added to the file set.
4. Case-insensitive cleanup: `Doorae`, `DOORAE`, `doorae`, plus the `[Dd][Oo][Oo][Rr][Aa][Ee]/gi` permutation that catches outliers like `DoorAE` (used in two `Test{Doorae}…` test classes in `packages/cluster/tests/test_mcp_templates_*.py`).

Root `pyproject.toml` workspace name explicitly set to `anygarden-workspace` to avoid collision with the cluster package, which now owns the bare `anygarden` name.

Per-area outcomes:
- **Python imports**: `import doorae` / `doorae_agent` / `doorae_machine` → `anygarden` / `anygarden_agent` / `anygarden_machine`. All three packages now `importlib.import_module`-able under the new names.
- **CLI scripts** (`[project.scripts]`): `doorae-server` / `doorae-agent` / `doorae-client` / `doorae-machine` → `anygarden-*`.
- **Environment variables**: every `DOORAE_*` (≈30 vars: `DOORAE_JWT_SECRET`, `DOORAE_TOKEN`, `DOORAE_LLM_GATEWAY_ENABLED`, `DOORAE_DEV`, etc.) → `ANYGARDEN_*`.
- **Data paths**: `~/.doorae/` → `~/.anygarden/`. SQLite default URL `sqlite+aiosqlite:///~/.doorae/doorae.db` → `…/~/.anygarden/anygarden.db`.
- **Frontend**: localStorage keys (`doorae_token`, `doorae_token_prelogin`, `doorae_is_guest`, `doorae_guest_room_id`, `doorae_guest_display_name`) and UI strings rewritten. Existing sessions will not survive — by design, no migration.
- **npm**: `@doorae/agent-ts` → `@anygarden/agent-ts`; bin `doorae-agent-ts` → `anygarden-agent-ts`.
- **Docs/configs**: `README.md`, `CLAUDE.md`, `Makefile`, `packages/cluster/Makefile`, all `docs/decisions/`, `docs/design/`, `docs/plans/`, `docs/runbook/`, `.env.example`, `.gitignore`, `.githooks/post-merge`, `packages/cluster/alembic.ini` (script_location + sqlalchemy.url), `packages/agent-ts/LICENSE` Copyright line.

Verification:
- `uv sync --all-packages --extra dev` resolves cleanly.
- Standalone imports succeed (`python -c "import anygarden"`, etc.).
- `pytest packages/cluster/tests` → 993 passed, 1 deselected (slow tests).
- `pytest packages/agent/tests` → 384 passed.
- `pytest packages/machine/tests` → 346 passed, 2 skipped.
- `cd packages/cluster/frontend && npm run build` → tsc + vite build OK (`anygarden-frontend@0.1.0` confirms package.json rename).

## Decisions

- **Deep rebrand in one commit vs. phased PRs.** A multi-commit phased rename (Phase 1 dirs+imports, Phase 2 env vars, Phase 3 data path, Phase 4 frontend, etc.) was the original plan. Collapsed to a single commit because: (a) the four-pass Perl pipeline produced a clean delta that builds and tests as a whole, (b) intermediate commits would be inconsistent (e.g. cluster code on `anygarden` but env vars still `DOORAE_*` — fails at import time), (c) reviewer effort is equivalent either way since the diff is mechanical.
- **No backward-compatibility shims for any surface.** Explicit user policy: import re-exports (`doorae = anygarden`), env-var fallbacks (`os.environ.get("ANYGARDEN_X", os.environ.get("DOORAE_X"))`), and data-path migration (`mv ~/.doorae ~/.anygarden`) were all rejected. Reasoning: the project is pre-stable, external users + operators are small enough to coordinate manually, and shim drift is a long-term tax that's worse than a one-time break. If shims are needed later they can be added incrementally.
- **Root workspace pyproject renamed to `anygarden-workspace`** rather than to `anygarden`, because that name belongs to the cluster package now. `anygarden-workspace` keeps the workspace marker purpose explicit and avoids the name collision that would block `uv sync`.
- **`worklogs/` and `docs/history/` excluded from substitution.** These are temporal records — rewriting them would falsify the historical narrative (e.g. a worklog for #387 said "renamed to drhub" — rewriting it to say "renamed to anygarden" would be a lie). Out of 504 initially-modified files, 138 worklogs + 3 docs/history were explicitly `git restore`-d after they were unintentionally swept up by `xargs grep -l 'doorae'`.
- **GitHub repo URL `github.com/e7217/doorae` left in place.** Users haven't decided whether to rename the GitHub repository. GitHub's redirect on rename is reliable, so the doc links will continue to work even if/when the repo gets renamed. Forcing this decision into the rebrand PR would conflate two separable choices.
- **`docs/plans/2026-04-20-*.md` untouched.** These are user-authored in-progress design docs sitting as untracked files. Touching untracked files via `xargs perl -i` would silently rewrite the user's work mid-draft. The substitution pipeline only modifies files surfaced by `git ls-files`, which excludes them naturally.
- **CLAUDE.md (untracked but workspace-authoritative) explicitly added to the substitution set.** It's the project's CLAUDE Code briefing document and must reflect the current state. Adding it via `{ git ls-files ...; echo CLAUDE.md; } | sort -u` keeps the special case localized.

## Result

- Repository contains zero `doorae` references in tracked files outside `worklogs/` and `docs/history/`.
- `pip install anygarden` / `anygarden-agent` / `anygarden-machine` workflows are consistent with the codebase. (PyPI uploads for the first two already exist at 0.7.1; `anygarden-machine 0.7.1` upload is still blocked by issue #393's rate-limit cooldown.)
- All Python package tests + frontend build pass under the new names.
- Pending follow-up:
  - `anygarden-machine` PyPI upload (tracked in #393).
  - User-visible breaking changes (env vars, data path, localStorage) need release-note coverage for any existing operator. Not addressed here.
  - Optional: rename `github.com/e7217/doorae` → `github.com/e7217/anygarden` once the team is ready to commit. Repo URLs in docs will follow GitHub's auto-redirect until then.
