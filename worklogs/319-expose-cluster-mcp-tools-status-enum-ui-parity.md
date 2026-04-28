# fix(agents+tasks): expose cluster MCP tools to all engines + status enum/UI parity (#319)

- Commit: `f0697c1` (f0697c15cbd0dd873482d1e04c0a5a10c24d3b99)
- Author: Changyong Um
- Date: 2026-04-29T00:42:39+09:00
- PR: #319

## Situation

In testroom4 the orchestrator/representative agent ``agent01-claude``
silently let assigned tasks rot — the most recent example, task
``4d476580`` ("호스트 리소스 점검"), was stamped ``failed
(pickup_timeout)`` by the goals sweeper after sitting on
``status='todo'`` past the watchdog window. The agent's own narration
read like a hallucinated cover-up: "환경 제약으로 mark_task_status 호출이
불가능, 시스템상 상태는 blocked로 유지". The user reported three
symptoms — orchestrator not executing todos, the UI only ever showing
``todo`` (in_progress / done / failed never visible), and status
changes seeming to require a manual refresh.

## Task

- Find why every claude-code (and reportedly codex / gemini-cli)
  orchestrator was unable to flip its own task status, despite the
  spawner correctly writing the cluster's HTTP MCP entry into
  ``.mcp.json`` / ``.codex/config.toml`` / ``.gemini/settings.json``
  (verified on the live agent dirs).
- Fix the root cause without breaking the orchestrator-only
  ``handoff_to`` MCP tool that already lives in the same code path.
- Bring the frontend status vocabulary in line with the values the
  goals sweeper actually stores so a ``failed`` task is rendered as
  "Failed" instead of falling through a missing-key branch.
- Make ``mark_task_status``'s allowed enum match the sweeper-written
  values so an agent that gives up cannot be forced into ``blocked``
  by a 4xx.

## Action

- ``packages/agent/doorae_agent/integrations/claude_code.py`` —
  in the orchestrator branch of ``_build_options`` rename the
  in-process MCP server key from ``"doorae"`` to ``"handoff"`` and
  drop the ``allowed_tools=["mcp__doorae__handoff_to"]`` whitelist.
  Long comment on lines 268-… explains the failure mode (name
  collision with the spawner-written cluster doorae entry plus a
  single-element whitelist that blocked every other cluster tool)
  for the next reader.
- ``packages/agent/tests/test_integrations/test_claude_code.py`` —
  flipped three assertions in ``TestHandoffTool`` to follow the new
  key (``mcp_servers["handoff"]``), added a guard that ``"doorae"``
  must NOT appear in ``mcp_servers`` (so a future refactor can't
  reintroduce the shadow), and asserted ``allowed_tools`` is *not*
  set on the orchestrator turn.
- ``packages/cluster/doorae/mcp/tools.py:35`` — extended
  ``TASK_STATUS_VALUES`` to include ``failed`` (and updated the
  comment to record the drift this fixes). The ``mark_task_status``
  JSON-Schema enum derives from the same constant, so the LLM-facing
  hint updated automatically.
- ``packages/cluster/tests/test_mark_task_status.py`` — added
  ``test_assignee_agent_can_mark_failed`` so the new enum membership
  is locked in.
- ``packages/cluster/frontend/src/components/right-rail/TasksSection.tsx``
  — added ``PauseCircle`` / ``XCircle`` imports, extended
  ``STATUS_ICON`` and ``STATUS_LABEL`` with ``blocked`` / ``failed``,
  introduced ``STATUS_ORDER`` for deterministic group rendering,
  pre-seeded the ``grouped`` reducer with all five buckets, taught
  the icon and title-strikethrough branches the new statuses, and
  taught ``cycleStatus`` to fall back to ``todo`` when the current
  status is system-set (``blocked`` / ``failed``).
- ``packages/cluster/frontend/src/components/TaskPanel.tsx`` —
  same vocabulary additions; also added ``Blocked`` / ``Failed``
  filter chips and the matching strikethrough branch.

## Decisions

The original plan (`.tmp/plan-319-cluster-mcp-exposure.md`)
considered three approaches to the MCP wiring:

- **A. Rename the in-process server** to ``handoff`` so it stops
  colliding with the spawner-written cluster entry. Picked.
- **B. Keep the ``doorae`` key, expand ``allowed_tools`` to enumerate
  every cluster tool.** Rejected: the SDK serialises dict-based
  ``mcp_servers`` through ``--mcp-config`` (subprocess_cli.py:247-275),
  and the same-name override behaviour against ``.mcp.json`` is not
  documented — it would be a time-bomb across SDK upgrades.
- **C. Replace ``handoff_to`` with a cluster-side handoff tool.**
  Rejected: out of scope, doubles the surface area, and the
  orchestrator-vs-room scoping is naturally enforced by keeping
  ``handoff_to`` in-process anyway.

A late mid-implementation revision flipped Decision 2 of the plan:
the original "expand the whitelist" stance was abandoned the moment
``ls /home/e7217/.doorae/agents/.../.mcp.json`` revealed an
admin-attached GitHub MCP entry sitting next to the doorae entry. A
narrow whitelist would have silently broken that integration. The
final form drops ``allowed_tools`` entirely and relies on the SDK's
permission-bypass mode (already in force) plus the spawner's
admin-curated ``.mcp.json`` for the trust boundary.

Why ``failed`` was added to the enum rather than collapsing it into
``blocked``: the goals sweeper distinguishes pickup-timeout from
execution-timeout via the ``error`` column, and that signal is more
useful when the status-axis preserves the failed/blocked distinction
than when both collapse into a single "stuck" state. Future
analytics on agent reliability would otherwise need to special-case
the ``error`` column.

Codex / gemini-cli adapters intentionally got no code change: the
spawner's ``doorae_default_entry`` already emits the right config
file shape for each engine and the live agent directories prove the
cluster doorae MCP is reachable on those engines today. If post-deploy
observation contradicts that, the system_prompt one-liner sketched in
plan Step 5 is the planned next move.

## Result

- ``packages/cluster``: 909 tests passed (1 deselected slow E2E).
- ``packages/agent``: 318 tests passed.
- ``packages/cluster/frontend``: ``npm run build`` (tsc + vite)
  clean — ``Blocked`` / ``Failed`` rows render with their own icons
  and color cues, ``STATUS_ORDER`` keeps the section order stable.
- ``ruff check`` clean on touched files.
- The pre-existing failed task ``4d47`` in the dev DB will surface
  as a "Failed" row on the next dev-server restart instead of
  silently disappearing through a missing-status-label fallback.
- Live ``mark_task_status`` round-trip still pending — verified by
  unit/integration tests in this PR; a manual UI walkthrough on
  testroom4 should be the first post-merge smoke check.
