# feat(agent,cluster): wire OpenHands MCP via shared .mcp.json (Phase 1) (#355)

- Commit: `3b7c8f1` (3b7c8f1...)
- Author: Changyong Um
- Date: 2026-05-09
- PR: #355

## Situation

Phase 0 of #355 added an in-process OpenHands adapter alongside the
three CLI engines but deliberately shipped with no MCP wiring — the
adapter constructed `Agent(llm=..., tools=[])` and ignored any
manifest. The cluster's MCP rendering code (`mcp_templates/merge.py`,
`mcp_templates/builtin.py`) only knew about claude-code, codex, and
gemini-cli, so even with admin-attached MCP servers in the database
the new engine never received them. The same heterogeneity that
caused #352 → #354 (per-engine MCP config branches diverging at
runtime) was about to repeat the moment a fourth engine appeared.

## Task

Make doorae's existing MCP rendering pipeline emit a config that
OpenHands accepts, without inventing a fourth manifest dialect:

- Reuse `.mcp.json` (claude-code's path) — OpenHands V1 SDK consumes
  the FastMCP config format, which is the shape claude-code already
  writes.
- Make the cluster `merge_for_engine`/`settings_path_for_engine`
  routes openhands the same JSON path as claude-code/gemini-cli.
- Make the OpenHandsAdapter read `.mcp.json` from agent root and
  forward the dict to `Agent(mcp_config=...)`.
- Keep the adapter resilient: a malformed manifest, a missing
  manifest, or an SDK build that doesn't accept `mcp_config` must
  all degrade to "no MCP, agent still alive". Silent degradation
  was the #292 dead-adapter trap and the same shape would re-emerge
  here if the manifest crashed the agent boot.

## Action

- `packages/cluster/doorae/mcp_templates/merge.py`:
  - `settings_path_for_engine` adds `"openhands": CLAUDE_SETTINGS_PATH`
    so the materializer writes the same `.mcp.json` for either
    engine choice.
  - `doorae_default_entry` (the doorae-cluster MCP server entry)
    routes openhands through the JSON-shape branch alongside
    claude-code and gemini-cli — the streamable HTTP shape with
    `{type, url, headers}`.
  - `merge_for_engine` extends the same JSON branch tuple to
    include `"openhands"`.
  - Module docstring updated to describe the openhands path
    explicitly so a future maintainer doesn't reintroduce a
    per-engine branch by accident.
- `packages/cluster/doorae/mcp_templates/builtin.py`:
  - `_three_engine_stdio` renamed to `_all_engines_stdio`. A
    back-compat alias preserves the old name so any pre-#355
    external import keeps resolving.
  - All five builtin templates (github, slack, notion, linear,
    filesystem) now ship `"openhands"` in `supported_engines`.
- `packages/agent/doorae_agent/integrations/openhands_engine.py`:
  - `_MCP_MANIFEST_PATH = ".mcp.json"` constant.
  - New `_load_mcp_manifest(path)` helper with a paranoid fallback
    surface — missing file, empty file, invalid JSON, non-object
    root, empty `mcpServers` map all collapse to `None`. Logs a
    structured warning on parse failures so an admin debugging "no
    tools showing up" can correlate.
  - `_get_or_create_conversation` reads the manifest once per
    Conversation, builds `agent_kwargs` with `mcp_config=` only when
    the helper returned a non-empty dict.
  - Agent construction now wraps a `_try_construct(extra)` closure
    that progressively falls back: first attempt with
    `mcp_config`; on `TypeError` (older SDK without that kwarg),
    log `openhands.mcp_config_rejected_by_sdk` and retry without.
    The system-prompt naming fallback layers on top of whichever
    kwargs path succeeded.
- `packages/agent/tests/test_integrations/test_openhands_engine.py`:
  - `TestLoadMcpManifest` (6 tests) — every paranoia branch.
  - `TestMcpConfigForwarded` (3 tests) — manifest present →
    `mcp_config` reaches Agent; manifest absent → kwarg omitted;
    SDK rejects `mcp_config` → adapter retries successfully.

## Decisions

The plan in `.tmp/plan-355-openhands-engine-migration.md` Phase 1
called for "MCP register via OpenHands typed tool system" but left
the integration shape open. Three options were on the table:

- **Push the MCP manifest dict directly through the WebSocket
  welcome frame** so the adapter never touches disk. Pure in-memory
  flow, no file dependency. Rejected because the cluster's
  `render_for_agent` already produces a `{path: content}` map that
  the machine materializer turns into files; bypassing that for
  openhands would mean two MCP plumbing paths in the system, and
  the cluster path is what claude-code's MCP plumbing also relies
  on. Diverging now would split future fixes.
- **Mount a doorae-specific manifest path (`.openhands/mcp.json`)
  even though the SDK accepts the same shape claude-code uses**.
  Rejected because the cluster code would need a fourth path entry
  and the materializer would write two files for an agent on
  openhands. We have nothing to gain from the separation — the
  shape is identical, FastMCP is what claude-code's manifest format
  already targets.
- **Reuse `.mcp.json` and read it from the adapter** (chosen).
  Single materialization path, no new cluster branches, no new
  files on disk. The merge.py/builtin.py changes are a couple of
  list-membership extensions instead of new dispatch tables.

What tipped the scale: the very pain we're solving is "engine-by-
engine MCP exposure code accumulates and breaks". Adding a fourth
manifest dialect for openhands would have re-introduced the exact
shape that caused #352 to be reverted in #354. Reusing claude-code's
path collapses two engines onto one renderer.

Explicitly rejected for Phase 1 (deferred, not abandoned):
- Provider-specific MCP transport overrides. OpenHands uses FastMCP,
  which advertises stdio + HTTP; the same JSON shape covers both.
  We don't need a transport switch yet.
- Subset filtering via `filter_tools_regex`. The SDK supports it;
  doorae doesn't model per-agent tool subsets at all yet, so
  there's nothing to wire it to.
- A retry/cache layer between manifest reads and Agent construction.
  The manifest is read once per Conversation creation (effectively
  once per room boot), so retry pressure is minimal. If multi-turn
  hot-reload becomes a feature in Phase 2/3, revisit then.

Assumptions worth flagging if they break later:
- OpenHands V1 SDK's FastMCP integration accepts the `{type: "http",
  url, headers}` shape. The docs reference FastMCP's client config
  format, which describes both stdio and HTTP variants, but the
  exact `headers` field semantics weren't explicit in the snippets
  I could fetch. If the SDK rejects it at runtime, the cluster
  would need a transport-aware branch for openhands while keeping
  claude-code on the same shape — a change isolated to
  `doorae_default_entry`.
- The `mcpServers` envelope key. If a future SDK rev moves to a
  different top-level key, `_load_mcp_manifest` would still parse
  the file but `_get_or_create_conversation` would forward an
  empty config. Adding a small remap layer in the helper would be
  the local fix.
- Agent constructor accepts `mcp_config` as a kwarg. If the SDK
  ships a more elaborate API (e.g. `agent.attach_mcp(...)` post-
  construction), the existing TypeError fallback degrades to no
  MCP gracefully but the proper integration would require a small
  refactor in `_get_or_create_conversation`.

## Result

Phase 1 complete. The cluster's MCP rendering now treats openhands
as a member of the JSON-shape engine group, so admin-attached MCP
servers + builtin templates (github, slack, notion, linear,
filesystem) + the doorae HTTP MCP server all reach the in-process
adapter through the same `.mcp.json` claude-code already consumes.
The OpenHands `Agent` receives the dict via `mcp_config` and
discovers the tools automatically.

Coverage: 23 / 23 OpenHands adapter tests pass (9 new for Phase 1);
82 / 82 cluster MCP tests pass (no regression from the
`_three_engine_stdio` → `_all_engines_stdio` rename or the
`supported_engines` extensions).

Still pending in the migration: Phase 2 (skills export), Phase 3
(DelegateTool sub-agents), Phase 4 (multi-provider catalog
expansion + LLM gateway integration), Phase 5 (validation
scenarios), Phase 6 (CLI engine deprecation marking). Phase 1
unblocks Phase 5 in the sense that any MCP-related comparison
between OpenHands and the CLI engines now has a real surface to
exercise.
