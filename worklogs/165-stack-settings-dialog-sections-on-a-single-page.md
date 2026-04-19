# refactor(agents): stack Settings dialog sections on a single page (#165)

- Commit: `9d46812` (9d46812fb3c52adc61271f62893df8d24bc01232)
- Author: Changyong Um
- Date: 2026-04-19T14:23:01+09:00
- PR: #165

## Situation

#158 shipped `AgentSettingsDialog` with a 200px left-rail nav and
four switchable panels (Overview / Manifest / Rooms / Activity).
Living with it surfaced the imbalance: the sidebar adds navigation
cost for only four destinations and hides three of them behind a
click. Admins opening the dialog to "just check this agent" still
have to click around to see Rooms or Activity. Separately, the
conditional-render strategy meant switching tabs mid-edit in
Manifest discarded unsaved changes — already flagged as a risk in
the design doc §6.2 but now trivially fixable by a layout change.

## Task

- Replace the nav + switch-pane layout with a single scrollable body
  where every section is always visible and mounted.
- Keep all section content intact — Overview/Manifest/Rooms/Activity
  panels themselves unchanged, only the shell rearranged.
- Preserve the dialog header (Agent settings — {name} ({engine}) with
  PresenceDot) and the `DialogDescription` accessibility affordance.
- Land the #158 design doc in git (it was created during
  brainstorming but accidentally left out of the #158 commit) and
  record this pivot as a Change history entry at its top so future
  readers understand the shipped-then-revised sidebar rationale.
- No change to `⋯` menu, to panel internals, or to
  `AgentRoomsDialog`'s thin-wrapper role for topology/DetailPanel.

## Action

- `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`:
  stripped `useState<SettingsSection>`, the `NAV_ITEMS` table, and
  the `<nav>` + grid columns. Replaced with a single scroll
  container that renders four `<Section>` wrappers in document
  order; each wrapper adds an `aria-labelledby` heading and a
  `[&>section]:py-5` divide-y seam for visual separation.
  `max-w-5xl` shrunk back to `max-w-4xl` since the 200px nail was
  removed, matching #158's pre-nav plan.
- `packages/cluster/frontend/src/components/AgentSettingsDialog.test.tsx`:
  rewrote the three cases. Nav-presence + tab-switching assertions
  replaced with "all four sections render simultaneously" and
  "sections appear in Overview → Manifest → Rooms → Activity order".
  Added an `apiFetch` mock at module level because Activity/Rooms
  panels now fire data effects on mount (previously gated by the
  nav-selection); jsdom's URL parser rejects relative
  `/api/v1/agents/...` paths without it.
- `docs/plans/2026-04-19-agent-settings-unified-dialog-design.md`:
  added (first time landing in git), with a new "Change history"
  section noting the #165 pivot, the reason, and the fact that §3's
  sidebar rationale is now historical context not current behavior.

## Decisions

From the mini-brainstorm in this conversation that preceded the
issue (#165 body captures the result):

- **(A) All four sections stacked** — picked. Matches user intent of
  "단일 페이지" after trying the shipped sidebar.
- **(B) Stack Overview/Rooms/Activity, Manifest in a collapsible or
  separate modal** — rejected. Splitting Manifest out reintroduces
  the "two places for agent settings" problem that #158 was meant to
  fix, and collapsing/expanding wouldn't materially reduce the
  perceived dialog height since Manifest is the section admins
  spend time in.
- **(C) Small sections only, Manifest behind a button** — rejected
  for the same "two places" reason plus it undoes the #158 goal of
  one unified entry.

What tipped the scale toward (A): the user directly chose it when
presented with the three options, and the biggest cost of (A) —
Manifest's size dominating the scroll — was judged acceptable
because admins who open Settings with Manifest in mind will scroll
straight to it anyway, and those who don't care about Manifest now
see Rooms/Activity without a click.

Assumption that would trigger revisiting: if the dialog height
becomes unwieldy on small laptops (Manifest's file tree + editor
has its own min-height), revisit with either a sticky section-jump
toolbar at the top or an in-section collapse on Manifest. No
evidence of this yet — left to the next user signal.

## Result

- `AgentSettingsDialog` now renders all four sections stacked; the
  `⋯` menu → Settings… flow shows the whole agent in one scroll.
- `npm run build` and `npm test` (244 passed, 25 files) both
  green on the refactor branch.
- Side benefit: unsaved Manifest edits survive scrolling to other
  sections because every panel is always mounted. The "unsaved edits
  lost on tab switch" risk from the #158 design doc (§6.2) is now
  moot.
- Still pending: manual visual verification vs DESIGN.md (warm
  neutral palette, whisper borders between sections, the new section
  heading typography). Planned before PR merge.
