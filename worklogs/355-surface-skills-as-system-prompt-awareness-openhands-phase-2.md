# feat(agent): surface skills as system-prompt awareness for OpenHands (Phase 2) (#355)

- Commit: `65415d4`
- Author: Changyong Um
- Date: 2026-05-09
- PR: #355

## Situation

After Phase 0 (the OpenHands adapter scaffold) and Phase 1 (MCP
wiring through the shared `.mcp.json`), the new engine still
ignored the per-agent skills the materializer was already dropping
into `<agent_root>/skills/<slug>/SKILL.md` (the
`machine.agent_dir` whitelist explicitly allows that prefix).
claude-code's SDK auto-discovers skills from project sources, so
the existing engine sees them for free; OpenHands has no analogous
discovery, so the LLM had no signal that any skills existed and
operators couldn't tell why a skill they had attached was being
ignored.

## Task

Make the OpenHands adapter surface available skills to the LLM
without crossing the line into runtime SDK exercise that this PR
can't safely cover. Constraints:

- Skill enumeration must be cheap (no full-body load — SKILL bodies
  routinely run thousands of tokens).
- Tolerant of malformed or absent SKILL.md files; missing /
  malformed → "no skills", agent boots normally. The #292 trap
  (silent degradation) and the #352 trap (engine-by-engine
  divergence) both apply here too.
- No new dependencies. doorae-agent doesn't currently require
  PyYAML, so the frontmatter parser has to be small and inline.
- Skills come ahead of the operator's `system_prompt` so the
  capability inventory precedes any task-specific narrowing.

## Action

- `packages/agent/doorae_agent/integrations/openhands_engine.py`:
  - `_SKILLS_DIR_NAME = "skills"` constant — mirrors the
    `machine.agent_dir._ALLOWED_PREFIXES` entry so a future rename
    surfaces in one place.
  - `_parse_skill_frontmatter(raw)` — minimal YAML-ish parser that
    extracts `key: value` pairs between the leading `---` fence and
    the closing `\n---`. Tolerant: missing fence, unterminated
    fence, comment lines, and quote-wrapped values (so a
    `description: "Use X: do Y"` keeps its embedded colon and
    sheds the wrapping quotes). Empty dict on malformed input.
  - `_load_skills_summary(skills_dir)` — walks `<skills_dir>`,
    reads each `<slug>/SKILL.md` frontmatter, drops entries
    without a description (a bare name in the prompt just wastes
    tokens), sorts alphabetically, returns a markdown block:
    `## Available skills\n\n- **<name>** — <first description line>`.
    Returns `None` for missing dir / empty dir / every skill
    malformed.
  - `OpenHandsAdapter._compose_system_prompt()` — combines the
    skills block with the caller's `system_prompt`. Skills first,
    then the caller prompt, separated by `\n\n`. Returns `None`
    only when both are absent so the construct fallback can omit
    the kwarg entirely.
  - `_get_or_create_conversation` now uses
    `self._compose_system_prompt()` instead of the raw
    `self._system_prompt`, so the skills block always reaches
    `Agent(system_prompt=...)` (or `system_message=...` via the
    Phase 0 fallback path) when present.
- `packages/agent/tests/test_integrations/test_openhands_engine.py`:
  - `TestParseSkillFrontmatter` (5 tests) — every parser branch
    including the quoted-value unwrap.
  - `TestLoadSkillsSummary` (6 tests) — missing dir, empty dir,
    skill without SKILL.md, skill without description, single
    skill rendered, alphabetical ordering across multiple skills.
  - `TestSkillsInjectedIntoSystemPrompt` (3 tests) — skills +
    caller prompt reach `Agent` in the right order; no skills →
    no block; skills only → block alone.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` Phase 2
called for "agent_dir's skills converted to OpenHands tool". Three
options were on the table:

- **Full Tool wrapping**: write a doorae-side adapter that turns
  each SKILL.md into an OpenHands `ToolDefinition` with proper
  `Action` / `Observation` / `Executor` classes. The LLM could
  then invoke a skill end-to-end with arguments. Rejected for this
  PR because the schema shape (per the OpenHands custom-tools
  guide: pydantic Action fields, Observation `to_llm_content`,
  ToolExecutor signature, `register_tool` registry) needs runtime
  validation against the live SDK to catch the kind of cross-rev
  shape changes my static investigation can't see. Landing this
  blind would set us up for the same `#292` silent-degradation
  trap.
- **Pre-load every SKILL body into the system prompt**: most
  faithful to "the LLM has the skill". Rejected because skill
  bodies are typically thousands of tokens of procedural guidance
  — multiplying that across every system prompt blows the context
  budget and duplicates content the LLM only needs at invocation
  time, not at agent boot.
- **Frontmatter-only awareness block** (chosen): list
  `name + description` per skill so the LLM knows the skill exists
  and can describe it when asked. Cheap (one line each), bounded,
  no SDK exercise. Operators can verify the integration by asking
  "what skills do you have?" — the answer is testable without
  invoking an LLM tool path.

What tipped the scale: the user wanted to push through all
remaining phases in this session. Full Tool wrapping requires
runtime experimentation that this session can't perform safely.
The awareness block ships value (operators can confirm skills
attached, LLM stops being blind to them) without depending on
schema details we'd otherwise have to guess.

Explicitly rejected for Phase 2 (deferred):
- Full SKILL → `ToolDefinition` wrapping. Documented in the
  commit body as the next iteration.
- PyYAML dependency. The repo's existing skills use the simple
  `key: value` shape only; the inline parser handles it without
  the install footprint.
- Body load on first tool-call. That's the right design for the
  full-Tool variant, but with awareness-only there's nothing to
  load lazily.

Assumptions worth flagging if they break later:
- SKILL.md files in this repo all use one-line scalar frontmatter
  (`name`, `description`). If a future skill uses nested YAML
  (lists, multi-line strings, anchors), the inline parser will
  drop or mis-handle them. Tests pin the supported shape so the
  failure mode is loud rather than silent.
- "Skills first, then caller prompt" ordering. If we later
  discover an LLM follows the *last* prompt section more
  faithfully, the order will need to flip — but at that point the
  fix is one line in `_compose_system_prompt`.
- Skills without descriptions are skipped silently. If a skill
  ever ships intentionally bare-name (e.g. for a tool-only LLM
  that doesn't need descriptions), the awareness block hides it.
  The current test set documents this as deliberate.

## Result

Phase 2 complete. The OpenHands `Agent` now receives a system
prompt that begins with an `## Available skills` block listing
every per-agent skill the materializer wrote, followed by the
caller's task-specific prompt. The block is omitted entirely when
no skills are present so we don't waste tokens on a stub header.

Coverage: 37 / 37 OpenHands adapter tests pass (14 new for Phase 2
on top of Phase 0's 14 + Phase 1's 9). No cluster / machine
changes; no other regression surface.

Still pending: full SKILL → OpenHands `Tool` wrapping (separate
follow-up requiring runtime SDK validation), Phase 3 (DelegateTool
sub-agents), Phase 4 (multi-provider + LLM gateway), Phase 5
(validation), Phase 6 (deprecation marking).
