---
title: Agent editor future UX (Option C follow-ups)
created: 2026-04-12
status: draft
---

# Agent editor future UX (Option C follow-ups)

This document captures the non-developer UX improvements we deferred
while landing the initial "agent manifest editor" stack (PR #8 backend
API, PR #9 admin UI). The shipped editor works end-to-end for
developer-type admins who are comfortable editing raw Markdown, YAML
frontmatter, and TOML / JSON — but it does not yet help an admin who
just wants to "add a skill" or "give this agent access to Notion"
without understanding the underlying file shape.

**None of these are scheduled.** They are parked here so that when we
come back to the editor with real usage evidence we have a concrete
starting point instead of a blank page.

Related prior art:

- `docs/plans/2026-04-11-per-agent-directory-skills.md` — Open
  question #1 ("`agent_files` 편집 UI") reserved this work for a
  later phase.
- `docs/decisions/002-per-agent-directory-with-server-manifest.md` —
  declarative reconcile model the editor sits on top of.

---

## 1. Skill library / template picker

**Problem.** Today the "New file" button in `AgentEditDialog` takes
a free-text path, and the admin is responsible for typing the full
`skills/<name>/SKILL.md` path and authoring the YAML frontmatter +
Markdown body. That is a dev-grade interaction — non-developers
don't know what the frontmatter keys are or what the `[SKILL: <name>]`
convention looks like.

**Proposed UX.**

- A "Browse skills" button next to "New file" that opens a modal
  catalog of skill templates.
- Each template card: name, one-line description, preview of the
  SKILL.md body, "Add to agent" button. Clicking the button inserts
  a new `agent_files` row under `skills/<name>/SKILL.md` with the
  template body already filled in; the admin can then tweak the
  body inline if they want.
- Multi-select support so an admin can batch-add "greeting +
  time-check + code-review" in one click.

**Open design questions.**

1. **Where do templates come from?**
   - (a) Hardcoded in the frontend bundle as a static list. Simplest;
     but every change ships a frontend build.
   - (b) Server-provided catalog endpoint (`GET /api/v1/skill-templates`).
     Admin can extend the catalog per deployment. Needs a new table
     or a filesystem scan of a `templates/` directory.
   - (c) Fetched from a community registry (e.g. a GitHub repo of
     curated skills). Needs a trust/review story.
   - **Recommendation:** start with (a) for the built-ins Doorae ships
     with, add (b) when a customer asks.

2. **Variable substitution.** Some skills want to parameterize the
   body — e.g. "review code in $LANGUAGE". We could define a simple
   `{{ variable }}` syntax and show a form after selection. For the
   first cut, keep templates parameter-free; admins edit the body
   inline if they need to customize.

3. **Conflict handling.** If `skills/greeting/SKILL.md` already
   exists and the admin picks the "greeting" template, the "Add"
   button should prompt before overwriting.

---

## 2. MCP server "marketplace" picker

**Problem.** MCP servers are configured today by hand-editing the
engine config file — `.codex/config.toml`, `.gemini/settings.json`,
`.claude/settings.json`. Each engine uses a different schema
(TOML vs JSON, different nesting, different keys) and the admin has
to know all of them.

**Proposed UX.**

- A "Browse MCP servers" button that opens a list of known MCP
  servers (name, description, required env vars, homepage link).
- Clicking a server shows an engine-aware picker: "Which engines
  should use this server?" with checkboxes for codex / claude-code /
  gemini-cli. For each checked engine, the editor writes the correct
  config fragment into the right file.
- The admin only ever sees high-level fields: server name,
  credentials (if any), "enabled for these engines". The file
  edits happen under the hood.

**Open design questions.**

1. **Source of truth for MCP server metadata.** Same three options
   as skill templates. Start with a hardcoded list of the MCP
   servers doorae customers commonly use (a few filesystem, git,
   web-fetch, a handful of "SaaS-integration" ones).

2. **Credential handling.** Some MCP servers need API keys
   (e.g. `NOTION_API_KEY`). These should go into the per-agent
   `engine_secrets` store, which is not yet wired through the
   REST API — see Phase X in the main plan doc. Until that lands,
   the MCP picker has to show the admin a "you'll need to set
   `NOTION_API_KEY` in the machine's environment" hint instead of
   taking the value through the form.

3. **Schema drift.** Each engine's config schema can change
   between versions. Mitigation: version-pin the schema per
   known-good engine release and show a warning if the detected
   engine version on the machine doesn't match. Longer-term,
   publish a JSON-Schema-per-engine that the UI validates
   against.

4. **Sandbox / write-guard interplay.** MCP servers that run local
   processes (e.g. filesystem MCP) widen the blast radius of the
   agent. The picker should surface a warning and require an
   explicit confirm for servers flagged as "privileged". Category
   tag on each entry: `safe` / `fs-write` / `network` / `shell`.

---

## 3. Syntax highlighting + per-file-type hints

**Problem.** The current editor is a plain `<textarea>`. No
highlighting, no indentation help, no format-specific
autocomplete. Admins editing `.codex/config.toml` get zero help
with TOML quoting, and `.gemini/settings.json` gives no hint when
a brace is missing.

**Proposed UX.**

- Switch the file editor pane to Monaco (VS Code's editor widget)
  loaded dynamically (only for the dialog) so the main bundle
  stays slim.
- Detect language from the file extension: `.md` → Markdown, `.toml`
  → TOML, `.json` → JSON, `.yaml`/`.yml` → YAML, `.env` → dotenv.
- Enable JSON schema validation where we have one (engine config
  files; see §2).
- Keep the current plain textarea as a fallback for the AGENTS.md
  section; Markdown in Monaco is a net wash versus the simpler
  textarea for a role/rules document.

**Open design questions.**

1. Bundle size. Monaco is roughly +2 MB gzipped. The admin page
   is already gated behind login so we can lazy-load it on dialog
   open without hurting the chat UX. Verify with a Lighthouse run
   after switching.

2. Theme. Monaco's default dark theme clashes with DESIGN.md's
   warm neutral palette; either pick the `vs-light` theme and
   accept the visual break, or build a custom `doorae-light`
   theme that maps Monaco tokens to our CSS variables.

---

## 4. "Clone from another agent" action

**Problem.** When setting up two similar agents (e.g. two codex
agents that differ only in role), the admin has to re-author
both `AGENTS.md` bodies and each skill file.

**Proposed UX.**

- On the AdminAgents table row, a "Duplicate" action next to
  "Edit manifest" copies the source agent's `agents_md` + every
  `agent_files` row into a new agent. The new agent starts
  stopped and with no room memberships (the admin still has to
  pick rooms and start it explicitly — "Duplicate" is not
  "Clone running state").

- Optionally, "Save as template" on the edit dialog lets the
  admin freeze the current manifest as a named skill-library
  entry (see §1), closing the loop.

**Open design questions.**

1. Engine change on duplication. Should the duplicated agent
   inherit the source's engine, or let the admin pick? Lean
   toward "inherit by default, editable in the create dialog".

2. `engine_secrets` handling. Duplication MUST NOT copy secrets
   across agents — each agent gets its own credential scope.
   The UI should show an explicit "secrets will not be copied"
   hint.

---

## 5. Live preview / "what will the materializer write?"

**Problem.** Because files are written by the machine-side
materializer, the admin has no way to verify "will my skill
actually show up in codex?" without starting the agent and
firing a message. The Phase 1.5 auto-inline step (skills/*/SKILL.md
bodies appended under `## Available skills` in AGENTS.md) adds a
second layer where the on-disk file differs from what's in the DB.

**Proposed UX.**

- "Preview" tab alongside the editor shows the exact bytes the
  materializer would write on next spawn — AGENTS.md with the
  auto-inlined sections, skills/ tree, engine config files.
- Read-only. Useful for sanity-checking that the skill body is
  where the admin expects it.

**Open design questions.**

1. Where does the compose logic live? Today
   `Spawner._compose_agents_md` is in `doorae-machine`. Option A:
   move the pure-function compose to a shared package both the
   machine and the server can import. Option B: expose a server
   endpoint `POST /api/v1/agents/{id}/preview` that returns the
   composed bytes for a given manifest, running the same Python
   helper server-side. (B) is simpler because no package split is
   needed but means the server has to carry a copy of the compose
   helper — that duplication is the exact trap Phase 0 tried to
   avoid for path validation.

---

## 6. History / undo

**Problem.** Today, once the admin clicks Save the old manifest is
gone. If they typo'd and realized five minutes later, they have to
reconstruct from memory.

**Proposed UX.**

- Every save captures the previous content as an `agent_file_history`
  row (or a single `agent_history.agents_md_blob` snapshot) with
  timestamp + author user_id. A "History" panel in the edit dialog
  lists the last N revisions and lets the admin restore one.
- 30-day retention by default.

**Open design questions.**

1. Storage footprint. Markdown bodies are small but a prolific
   admin could churn through thousands of revisions. Cap to last
   N per agent + delete after 30 days.

2. Diff view. Worth it? A simple "revert to this revision" plus
   side-by-side plain text is probably enough for V1.

---

## 7. Hot-reload (live apply without respawn)

**Problem.** Changes take effect only on the next `request_start`.
An admin fixing a typo in AGENTS.md has to click Save → Stop → Start
to see it. Slow and easy to forget.

**Proposed UX.**

- On save, show "Apply now?" checkbox. When checked, trigger
  `POST /agents/{id}/start` server-side (which already stops and
  restarts).
- Implementation-wise this is essentially a one-line helper on the
  frontend. The real question is whether "apply now" should be the
  default.

**Open design questions.**

1. Default on or off? Off is safer (no surprise downtime on every
   save), but friction. Propose: off for agents that are currently
   running in a shared room with messages in flight, on for
   stopped/crashed agents.

2. Per-engine reload semantics. See the main plan doc Open
   question #2 — each engine has a different file re-read
   cadence, so "hot-reload" is effectively "kill + respawn" for
   every engine in Doorae today. That's fine for V1.

---

## When to revisit

These items are gated on **real usage evidence** from the shipped
editor, not on speculation. Specifically:

- Skill library: revisit when we see three or more admins
  independently authoring the same skill.
- MCP marketplace: revisit when more than two customers ask how to
  wire an MCP server, or when a non-developer admin tries to set
  one up unassisted.
- Monaco / syntax highlighting: revisit when we get a bug report
  of a broken TOML or JSON due to missing quote / brace.
- Clone: revisit when we see the first "I want two agents that are
  almost identical" pattern in the audit log.
- Live preview: revisit when the first Phase 1.5 auto-inline
  surprise hits production.
- History / undo: revisit after the first reported "oops I
  overwrote my AGENTS.md".
- Hot-reload: revisit when a deployed team complains about the
  stop-start cycle.

Until those signals show up, the plain-textarea + file-tree editor
we shipped is enough.
