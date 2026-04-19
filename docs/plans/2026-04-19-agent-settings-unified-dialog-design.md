# Agent Settings — Unified Dialog Design

**Date**: 2026-04-19
**Status**: Implemented in #158, restructured by #165 (see Change history)
**Scope**: `packages/cluster/frontend/` — admin-facing agent settings UX

## Change history

- **2026-04-19 (#165)** — Replaced the left-rail nav layout chosen
  below with a single-page stacked layout. The sidebar hid three of
  four sections behind a click without a clear payoff for only four
  destinations; stacking gives the admin the whole agent in one
  scroll. All four sections are now always mounted, which also
  retires the "unsaved Manifest edits lost when switching away" risk
  flagged in §6.2. The left-sidebar rationale below in §3 (Design) is
  preserved for historical context — it describes the shipped-then-
  revised layout, not the current one.

## Problem

The `⋯` (AgentSettingsMenu) trigger on an agent row currently fans out
to four independent dialogs:

- `AvatarPickerDialog` — emoji/avatar picker
- `AgentEditDialog` — file-tree + editor for manifest (AGENTS.md,
  `skills/`, engine config)
- `AgentRoomsDialog` — attach/detach rooms
- `AgentHistoryDialog` — read-only activity log

Plus inline menu items for **Copy agent ID**, **대화 맥락 공유 제외**
toggle, and **Delete agent**.

Two problems:

1. The menu has seven-plus entries that all target the same agent but
   feel like unrelated features. There is no single place to see "what
   is this agent?" — the admin has to open three dialogs in sequence
   to check the avatar, the manifest, and recent activity.

2. **Copy agent ID is broken in practice.** The handler calls
   `navigator.clipboard.writeText(agentId)` but silently swallows
   failures (insecure-context contexts, permissions denied) and gives
   no success feedback. An admin clicking "Copy agent ID" has no way
   to know whether anything happened.

## Goals

- One **Settings…** entry point on the `⋯` menu that opens a dialog
  containing every non-destructive capability: overview, manifest,
  rooms, activity, and a visible-and-copyable agent ID.
- Keep `Delete agent` and `대화 맥락 공유 제외` in the `⋯` menu —
  destructive and frequent-toggle actions respectively, where the
  menu shape is already optimal.
- Fix "Copy ID" by rendering the ID as inline text with a copy button
  that shows "Copied" feedback, mirroring the regen-token / machine-
  token pattern already used in `AdminMachines.tsx`.
- No regression in existing entry points — Sidebar row menus,
  AdminMachines rows, and the topology DetailPanel each keep working.

## Non-goals

- Changing the manifest editor's internals (file tree, virtual
  AGENTS.md row, save semantics). The existing behavior is preserved
  wholesale — it just loses its outer Dialog wrapper.
- Introducing new agent settings. Any "while we're here let's add X"
  items are out of scope.
- Merging the `대화 맥락 공유 제외` toggle into the dialog. It stays
  in the menu because it's a one-click toggle and the menu entry
  already communicates state via a trailing check mark.

## Design

### Menu shape (new `AgentSettingsMenu`)

```
┌─ ⋯ ─────────────────────────────────┐
│  ⚙  Settings…                        │  ← opens unified dialog
│  ─────────────────────────────────── │
│  🫥  대화 맥락 공유 제외       ✓    │  ← toggle (unchanged)
│  ─────────────────────────────────── │
│  🗑  Delete agent                   │  ← red, destructive
└──────────────────────────────────────┘
```

The old five-item stack (Edit avatar / Edit manifest / Manage rooms /
Activity / Copy agent ID) collapses into **Settings…**.

### Dialog shape (new `AgentSettingsDialog`)

```
┌─────────────────────────────────────────────────────────────┐
│  Agent settings — {name} ({engine})        ● idle      [X]  │
├──────────────┬──────────────────────────────────────────────┤
│ Overview     │                                              │
│ Manifest     │        (selected section renders here)       │
│ Rooms        │                                              │
│ Activity     │                                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

- Left rail: four nav items, height-aligned. Active item gets the
  brand-tint background used elsewhere in the frontend.
- Right pane: the active section's content. Overflow inside the
  pane is the section's own concern — Manifest keeps its own 2-col
  tree+editor grid, Activity a simple scroll list, etc.
- Width: `max-w-5xl` (≈ 1024px). One step up from AgentEditDialog's
  `max-w-4xl` to accommodate the 200px left rail without squeezing
  the manifest's existing 240px file tree + editor.
- Footer actions are section-scoped — the Manifest panel keeps its
  own Save / Close row; Overview's name edit auto-saves on blur.
  The dialog itself has no bottom toolbar.

### Section 1 — Overview

```
┌──────────────────────────────────────────────────────┐
│  ┌────┐                                              │
│  │ 🤖 │  ← click to edit avatar                      │
│  └────┘                                              │
│                                                      │
│  Name     [ greeting-bot             ]  ← inline     │
│                                                      │
│  ID       agent_abc123…                  [ Copy ]    │
│                                                      │
│  Engine   claude-code                                │
│  State    ● idle                                     │
│  Created  2026-03-15                                 │
└──────────────────────────────────────────────────────┘
```

**Avatar** — clicking the avatar tile expands the emoji picker
inline below (not modal-in-modal). The picker is the current
`AvatarPickerDialog` body extracted into an `AvatarPickerPanel`
component. Save on pick, collapse the picker.

**Name** — inline text input. Persists on blur or Enter via
`updateAgent({name})` (already supported by the `useAgents` hook at
`packages/cluster/frontend/src/hooks/useAgents.ts:160`). Error surface
is a tooltip below the field.

**ID** — monospace text + `Copy` icon button. Copy writes
`agent.id` to the clipboard and flips the button to `Copied` for
2 seconds (same pattern as `AdminMachines.tsx:780-783`). This is the
fix for the broken-in-practice copy flow.

**Engine / State / Created** — read-only. State renders with
`PresenceDot` using `deriveAgentOnline(actual_state)`, matching the
existing manifest dialog's header.

### Section 2 — Manifest

The current `AgentEditDialog` body rendered as-is. The file tree
(virtual `AGENTS.md`, engine-filtered prefixes, attached skills
section, new-file/new-skill flows, upload, save semantics) is
preserved wholesale. Only the outer `<Dialog>` / `<DialogContent>` /
`<DialogHeader>` wrappers are stripped — the header "Edit manifest —
{name}" moves to the AgentSettingsDialog header, and the
Save/Close footer stays inside this panel.

### Section 3 — Rooms

The current `AgentRoomsDialog` body rendered as-is. The existing
attach/detach UI, search, and list are preserved.

### Section 4 — Activity

The current `AgentHistoryDialog` body rendered as-is (it is only
63 lines — a scrollable list of lifecycle events with colored dots).

## Component decomposition

```
AgentSettingsDialog            (new, the outer <Dialog> shell)
 ├── OverviewPanel             (new)
 │    └── AvatarPickerPanel    (extracted from AvatarPickerDialog)
 ├── ManifestPanel             (extracted from AgentEditDialog)
 ├── RoomsPanel                (extracted from AgentRoomsDialog)
 └── ActivityPanel             (extracted from AgentHistoryDialog)

AgentRoomsDialog               (kept as a thin wrapper — only topology
                                DetailPanel still opens it directly)
 └── RoomsPanel                (same component reused)

AgentEditDialog                (removed — its only callers migrate)
AvatarPickerDialog             (removed)
AgentHistoryDialog             (removed)
```

**Why keep `AgentRoomsDialog` but not the other three?**
`topology/DetailPanel.tsx:6` opens `AgentRoomsDialog` independently
from the Settings menu — the topology view wants a lightweight
"just rooms" affordance. Forcing that path through the full
AgentSettingsDialog would open an oversized dialog for a focused
intent. The thin wrapper preserves the topology UX while the panel
itself is shared.

## Data flow

No new API surface. Every mutation goes through handlers the hook
already exposes (`useAgents` at
`packages/cluster/frontend/src/hooks/useAgents.ts`):

- `updateAgent({name})` — name inline edit
- `updateAgent({avatar_kind, avatar_value})` — avatar pick
- `updateAgent({agents_md, agents_md_set})` — manifest AGENTS.md
- `upsertAgentFile(id, path, content)` — manifest non-virtual files
- `deleteAgentFile(id, path)` — manifest deletions
- `addAgentToRoom`, `removeAgentFromRoom` — rooms
- `GET /api/v1/agents/{id}/activity?limit=50` — activity (fetched
  directly by `ActivityPanel`, same as today)

The dialog receives these handlers as props from the hook-owning
component (Sidebar or AdminMachines), rather than calling the hook
itself — matches the prop-drilling pattern already in use.

## Caller migration

| Caller                        | Before                                       | After                                       |
|-------------------------------|----------------------------------------------|---------------------------------------------|
| `Sidebar.tsx`                 | Opens 4 dialogs from menu items              | Opens `AgentSettingsDialog` from "Settings…" |
| `AdminMachines.tsx`           | Opens 4 dialogs from menu items              | Opens `AgentSettingsDialog` from "Settings…" |
| `topology/DetailPanel.tsx`    | Opens `AgentRoomsDialog` directly            | Unchanged (still opens `AgentRoomsDialog`)   |

Both Sidebar and AdminMachines currently hold local `useState` for
each dialog's `open` flag plus a `selectedAgentId`. After migration
they each hold one `settingsOpen` flag and one `selectedAgentId`.

## Initial selected section

Always **Overview** — the `⋯` menu collapses to a single "Settings…"
entry, so there is no caller that needs "open directly to
Manifest" / "open directly to Activity". Within a session, the
dialog does not remember the last-visited section (YAGNI — if usage
patterns show admins bouncing between tabs, we can add
`localStorage`-backed persistence later).

## Error handling

- **Name save failure** — revert the input to the previous value,
  show an error tooltip below the field, do not close the dialog.
- **Avatar save failure** — keep the old avatar visible, show an
  inline error in the picker area, leave the picker open so the
  admin can retry or pick a different value.
- **Copy ID failure** — clipboard API can reject in insecure
  contexts. Fall back to selecting the ID text so the admin can
  copy manually, and show "Clipboard unavailable — text selected"
  in place of "Copied".
- **Manifest save failure** — unchanged from today (error banner
  inside the panel).

## Testing

- New `AgentSettingsDialog.test.tsx` covers:
  - Left rail renders four sections; clicking switches the right pane.
  - Opens on Overview by default.
  - Overview: name inline edit calls `updateAgent({name})` on blur.
  - Overview: avatar click expands `AvatarPickerPanel`.
  - Overview: Copy ID writes to clipboard and shows "Copied".
  - Overview: Copy ID falls back gracefully when `clipboard.writeText`
    rejects.
- Existing `AgentEditDialog.test.tsx`, `AvatarPickerDialog.test.tsx`,
  `AgentSettingsMenu.test.tsx` migrate:
  - `AgentSettingsMenu.test.tsx` — assertions about individual menu
    items for Edit avatar / Edit manifest / Activity / Copy ID
    disappear; new assertions for the "Settings…" item remain.
  - Avatar-picker tests move to a `AvatarPickerPanel.test.tsx`
    that instantiates the panel without a Dialog wrapper.
  - Manifest-editor tests stay but update their render harness to
    mount `ManifestPanel` directly rather than `AgentEditDialog`.
- `AgentHistoryDialog.test.tsx` (if present — check during
  implementation) migrates to `ActivityPanel.test.tsx`.

## Open questions

None that block implementation. Two items flagged for future work:

1. **Last-visited section persistence** — if admins end up spending
   time bouncing between Manifest and Activity (e.g. during
   debugging sessions), remember the last active section per agent.
2. **Topology DetailPanel's RoomsDialog** — if additional agent
   actions ever appear on the topology panel (e.g. restart, edit
   manifest), reconsider whether it should open the full Settings
   dialog instead of keeping one-off dialogs.
