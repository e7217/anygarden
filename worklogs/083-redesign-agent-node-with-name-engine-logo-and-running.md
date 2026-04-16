# feat(topology): redesign agent node with name + engine logo + running pulse (#83)

- Commit: `03333ca` (03333ca2f71b60e077c9ebac0285b30446a43d1a)
- Author: Changyong Um
- Date: 2026-04-17T01:46:01+09:00
- PR: #83

## Situation

The `/topology` graph view (landed in #75) rendered every AgentNode as a 64×64 circle whose only content was a generic lucide icon keyed off the engine string (`Zap` for codex, `Sparkles` for claude, `Cpu` for gemini). When a room had several agents attached, they were visually indistinguishable — users could not tell which dot was which agent, and the engine names below were truncated after six characters. The node also had no affordance to signal that an agent was actively working versus merely alive.

## Task

- Replace the circle with a layout that shows the **agent name** (`label`) as the primary label, falling back to ellipsis when the name is long.
- Swap the lucide placeholders for the **official brand logos** of Claude / Codex / Gemini, with a safe fallback for unknown engines.
- Add a **subtle "running" animation** so users can see at a glance which agents are currently executing, without drowning the canvas in motion.
- Stay within DESIGN.md: single Notion Blue accent, whisper-weight borders, sub-0.05 shadow stack, 8px spacing grid.
- Do not break #82's planned hover-opacity dimming on the canvas wrapper.
- No backend changes — `label` / `engine` / `actual_state` are already on `AgentNodeData`.

## Action

- **New dependency**: `@lobehub/icons@5.4.0` added to `packages/cluster/frontend/package.json`. Imports use sub-paths (`@lobehub/icons/es/Claude|Codex|Gemini`) so only the three logos we render land in the bundle.
- **New `packages/cluster/frontend/src/components/topology/nodes/AgentNode.css`**: structural pill layout (140×44, 12px radius, flex row with 8px gap), the `@keyframes topology-agent-pulse` box-shadow ring, `.agent-node--running` that attaches the animation, and a `@media (prefers-reduced-motion: reduce)` block that kills it.
- **`AgentNode.tsx`** rewritten (`packages/cluster/frontend/src/components/topology/nodes/AgentNode.tsx`):
  - `engineIcon()` helper deleted; replaced by a named-export `EngineGlyph` component whose branching is driven by `engine.toLowerCase().includes(...)` so `claude-code`, `gemini-cli`, and uppercase variants all route correctly. Unknown engines fall back to lucide `Bot`.
  - JSX restructured as: `[logo 16×16] [label flex:1 ellipsis] [state dot 6px]`.
  - Dynamic styles (`background` tint from `ENGINE_TINT`, `border` color/width from `agentStateColor()` + selection) stay inline; everything else moved to class names.
  - `aria-label` and `title` retain the `Agent {label}, engine {engine}, state {state}` pattern.
- **`useGraphLayout.ts:14`**: `NODE_SIZE.agent` updated from `{64,64}` to `{140,44}` so dagre lays out the new pill shape without overlap.
- **New `AgentNode.test.tsx`** covering six branches of `EngineGlyph`: claude, claude-code, codex, gemini-cli, unknown fallback, and case-insensitivity. `@lobehub/icons/es/{Claude,Codex,Gemini}` are `vi.mock`ed so the runtime doesn't pull antd-style into the vitest environment.

## Decisions

Rationale mined from `.tmp/plan-83-agent-node-redesign.md` (§3 "Design").

- **Pill 140×44 over circle-keep-with-small-label or full card**: a 64px circle with a bottom-anchored name only fits ~6 characters — useless for identification. A 160×72 card would break dagre's vertical density with `nodesep: 48`. The pill widens horizontally while keeping rank spacing, matching the existing `rankdir: TB` layout, and lets ~14 characters of the name show before ellipsis.
- **`@lobehub/icons` over simple-icons or hand-copied SVGs**: simple-icons has no dedicated Codex-CLI or Gemini-CLI mark. Copying SVGs into the repo would fragment licensing and require manual updates. `@lobehub/icons` is MIT, tree-shakeable, React-native, and maintained specifically for the AI-engine brand set. Risk: coupling to a third-party brand package — mitigated because the `EngineGlyph` wrapper isolates the import surface to one file.
- **Sub-path imports (`@lobehub/icons/es/Claude`) over barrel `@lobehub/icons`**: the barrel entry re-exports every brand in the catalog. Sub-path imports bring only the three icons we actually render, and `Codex`'s colored variant has no `.Color` namespace so a single consistent pattern wouldn't work across all three anyway. (Claude/Gemini use `.Color`; Codex uses the default mono glyph.)
- **`box-shadow` ring pulse over `border-color`, `opacity`, or dot-only pulse**: `border-color` would jump between 1px/2px borders depending on selection; `opacity` would collide with #82's planned wrapper-opacity dimming (direct conflict — the two animations would visibly cancel); dot-only pulse is too faint at dagre's density. Box-shadow is paint-only, GPU-friendly, and composes cleanly with wrapper opacity. Ring capped at 4px so it doesn't invade neighboring nodes.
- **Alpha 0.35, 1.8s cycle**: low enough that ten concurrent running agents don't strobe the canvas, slow enough that it reads as "heartbeat" rather than "alert". Tunable in one place (`AgentNode.css`) if user testing wants adjustment.
- **`actual_state === 'running'` as the single pulse trigger**: explicit narrow condition rather than `ALIVE_AGENT_STATES`. `starting`/`stopping` get a grey border but no pulse — the animation should mean "actively executing", not just "not dead".
- **Rejected for scope**: variable pill width for very long agent names (would require dagre re-measurement), dark-mode palette (app is single-theme), and WS-driven real-time state (deferred to #84).

Assumption worth revisiting: if the backend starts emitting engine strings that don't contain `claude` / `codex` / `gemini` substrings (e.g. a new provider), they'll silently fall back to the `Bot` glyph — acceptable, but a signal to extend `EngineGlyph`.

## Result

- `/topology` now renders each agent as a pill with its name + brand logo + state dot. Running agents pulse in Notion Blue; reduced-motion users see static pills.
- `npm run build` passes. TopologyPage chunk: 355.47 KB raw / 111.73 KB gzip (includes the new @lobehub/icons sub-path imports).
- Six new unit tests pass; full frontend suite (78 tests) stays green.
- Closes #83. #82 (hover flicker) and #84 (active-room highlighting) remain as separate follow-ups — the pulse was designed not to collide with #82's planned opacity dimming.

## Codex review follow-up

Observation (post-review): Codex flagged three blockers / high-priority items:

1. **Engine coverage was too narrow.** The original `EngineGlyph` only routed `claude`/`codex`/`gemini`, yet the backend (`doorae_agent.integrations.ENGINES`) emits seven IDs: `claude-code`, `codex`, `gemini-cli`, `openhands`, `deep-agents`, `openai`, `anthropic`. Silent `Bot` fallback for `openhands`, `openai`, `anthropic` regressed brand recognition.
2. **Accessibility redundancy.** The decorative glyph and state dot were not marked `aria-hidden`, and the unused `aria-label="unknown engine"` on the fallback `Bot` duplicated info already on the outer wrapper's `aria-label`.
3. **Padding broke the 8px grid** (`padding: 0 10px`) and tests only exercised `EngineGlyph` branches, never `AgentNode` itself.

Action: expanded mapping to cover all seven backend engine IDs (Claude family → `Claude.Color`; OpenAI family → `Codex` mono; Gemini family → `Gemini.Color`; OpenHands → `OpenHands.Color`; `deep-agents` and unknowns → lucide `Bot`). Verified icon availability in `node_modules/@lobehub/icons/es/` (`Claude`, `Codex`, `Gemini`, `OpenAI`, `Anthropic`, `OpenHands` all present; no dedicated DeepAgents mark, hence the `Bot` fallback). Added `aria-hidden="true"` to glyph + dot, centralized semantics on the outer `aria-label` (`Agent {label}, engine {engine|unknown}, state {state}`). Bumped padding to `0 12px` (8px grid + 4px offset, documented inline). Added six `AgentNode`-level unit tests: running/idle class toggle, aria-label content, long-label smoke test, aria-hidden coverage, empty-engine fallback. Mocked `@xyflow/react` so `AgentNode` renders without a `ReactFlowProvider`.

Result: `npm test` 88/88 pass (was 78; +10). `npm run build` passes; TopologyPage chunk grew from 355 KB → 378 KB raw due to three additional `@lobehub/icons` sub-path imports — acceptable because the engine brand signal is now complete.

Scope-excluded (follow-ups not in this fixup):
- Pulse pseudo-element + `transform` optimization deferred — current `box-shadow` approach still respects DESIGN.md's sub-0.05 opacity shadow rule (alpha 0.35 is on the *animated* ring, not the static card shadow).
- Manual ESM runtime verification of `@lobehub/icons/es/*` sub-paths covered by `npm run build` + `npm test` passing.
