# feat(agent-settings): tabs, provider-based model connection and three-step creation (#715)

- Commits: `e8144f4` (four tabs), `ae6e1c3` (provider instead of connection mode), `1946805` (three-step creation, Codex login) on branch feat/715-agent-settings-tabs
- Author: Changyong Um
- Date: 2026-09-28
- PR: — (Part B of #715; Part A was #724)

## Situation

Agent settings had grown to eight sections stacked in one scrolling modal, with buttons that looked like tabs but only called `scrollIntoView()`. At 1280×720 the content area was about 350 px. The model connection mixed a "connection mode" select (Pi provider vs. direct server) that read like two engines, a card nested in a card, credential name/key fields shown even with "No authentication", and a reasoning select that was always disabled for Pi (its catalog has no levels and the runtime never passes one). Agent creation was a single long form with the same connection model and a dead LLM Gateway branch (the gateway was retired; the API always returns `source: "builtin"`). Codex agents rely on the machine's `codex login`, which the UI could not show; Part A (#724) added the daemon report.

## Task

- Group the eight destinations into a few real tabs; keep one Agent settings entry point and keep unsaved edits when switching.
- Make the model connection read as "provider → model → authentication", show credential inputs only when needed, keep advanced options reachable, hide unsupported reasoning.
- Split agent creation into steps without changing its create → credential → endpoint sequence or retry idempotency.
- Show the machine's Codex login status where the Codex model is chosen, with a re-check.
- Remove the dead gateway branch and unused strings.

## Action

- `packages/cluster/frontend/src/components/AgentSettingsDialog.tsx`: Radix `Tabs` with Settings (overview, model connection, instructions), Work (rooms, responsibilities, tasks), Workspace, Activity. Every `TabsContent` uses `forceMount` and is hidden with `data-[state=inactive]:hidden`; switching resets the body scroll. Settings has its own "Jump to" links (select on mobile) that scroll within the tab. Activity polls only while its tab is active; "show turn" from the overview switches to Activity. Section test IDs are unchanged.
- `components/agent-settings/ModelConnectionPanel.tsx`: a single "Provider" select replaces "Connection type": Pi provider / Machine's Codex login (OpenAI), and either "<name> · <host> (my server)" or "+ Add model server…". Codex can now add a server, and a Codex server connection can switch back to the machine login (`PUT /endpoint {base_url: null, model: null}`, moved here from `DirectEndpointPanel`). Reasoning renders only when the catalog has levels for the saved connection. Shows `CodexLoginStatus` for the default Codex connection.
- `components/agent-settings/DirectEndpointPanel.tsx`: no nested card or duplicate heading; URL, model, authentication, then an "Advanced" disclosure (provider ID, protocol; Codex shows "Responses (required by Codex)"). Authentication select: none / stored credentials / "+ New API key…"; key inputs appear only for a new or replaced key; replace/delete act on the selected credential. Loaded models become a select with "Enter manually…" and a "(not served by this server)" entry for a saved model the server no longer lists; a single served model is picked for an empty draft. An empty provider ID defaults to the URL host name.
- `components/agent-settings/PiNativeAuthPanel.tsx`: drops its own card, heading and description.
- New `components/agent-settings/CodexLoginStatus.tsx`: reads `GET /machines/{id}/engines` (`auth_status` from #724), shows ChatGPT account / API key / other / sign-in required / unknown, and "Check again" posts the existing engine check then polls until `auth_checked_at` changes.
- `components/CreateAgentDialog.tsx`: steps Basics → Engine & model → Access & rooms with a stepper, Back/Next, and all steps mounted (hidden) so going back keeps input. "Runs on <machine>" and the workspace note moved to Basics. The Pi select uses the same provider wording; the Pi provider ID field is labelled "Provider ID". Codex shows `CodexLoginStatus`; a missing login warns but does not block. Gateway optgroup removed; `useAgents.ts` `source` type narrowed to `'builtin'`.
- `components/agent-settings/OverviewPanel.tsx`: connection summary reads "<provider> (my server) · <model>" and "Machine's Codex login · <model>".
- i18n (`admin.ts`, `agentSetup.ts`): new ko/en strings; 12 unused keys removed from both locales.
- Tests: dialog tabs and mount preservation, provider select cases (Codex add server, named server, switch back to login, Pi hides reasoning), direct endpoint key entry / host default / manual model, Codex login states and re-check, create steps and gating; AdminMachines create-flow tests updated for steps.

## Decisions

- Tabs with `forceMount` vs. unmounting tabs vs. keeping the scroll page: unmounting would drop unsaved Manifest/workspace edits (an acceptance criterion and DESIGN.md §9); keeping the scroll page left the 350 px problem and the settings/work mix. Radix Tabs gives keyboard/ARIA behaviour from the existing `ui/tabs.tsx`. Radix renders forced panels without `hidden`, so inactive panels are hidden with a data-state class.
- One provider select instead of a mode select: both modes run the same engine (Pi `--provider`, a direct server is a custom provider in `models.json`; Codex likewise), so "provider" is the accurate concept. Pi has no provider catalog, so the Pi option stays a typed provider ID rather than the list drawn in the mockup. One server per agent is kept (current `/agents/{id}/endpoint` API); a shared server registry is out of scope.
- Default provider ID from the URL host: hiding the field under Advanced would otherwise leave "Apply" disabled for new servers.
- Creation keeps Codex on the machine login; adding a Codex server at creation would require sending a provider ID the current create path only sends for Pi. It remains available right after creation in settings.
- No-login Codex warns instead of blocking (the plan's D6): machines can be signed in after the agent exists, and older daemons report unknown.
- Models are loaded on demand ("Load models"), not automatically on open, to avoid probing servers every time settings open. Revisit if admins expect the list without a click.

## Result

Agent settings open on a Settings tab with direct links to overview, model connection and instructions; work, workspace files and activity are separate tabs that keep their state. The model connection for a direct Pi server shows URL, model and "No authentication" with no credential fields and no reasoning; Codex shows its model, reasoning and the machine login status. Creation is three steps. Verified: frontend 755 tests passed, `npm run build` passed; checked in an isolated dev server (scratch DB, ports 8021/5181) at 1440 px (settings tabs, Pi server connection, Codex login "Sign-in required") and 390 px (create steps 1–2). The first CI run failed `e2e/agent-setup.spec.ts`, which compared the name and engine controls on one page; the spec now steps to Engine & model before comparing heights and to Access & rooms before submitting (`b2e80db`). All 31 Playwright E2E tests then passed locally, including agent setup at 375/768/1024/1440 px in light and dark themes. Not checked: the settings dialog in dark theme and at 768/1024 px.
