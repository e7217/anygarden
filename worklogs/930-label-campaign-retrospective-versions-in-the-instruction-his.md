# fix(campaigns): label campaign retrospective versions in the instruction history and show their reason (#930)

- Commit: `b17f423` (b17f4233ca4b01c6994e37acc0f8498044f0a251)
- Author: Changyong Um
- Date: 2026-10-26T16:04:45+09:00
- PR: #930

## Situation

The instruction history dialog already showed versions created by `user`, `agent`, and `system` sources with human-readable badge labels. When the campaign retrospective feature (#930) began writing new instruction versions (source = `campaign`) after each cycle, those entries appeared in the history with a raw `"campaign"` string as the badge label — no i18n, no friendly display. Additionally, the retrospective writes a `rationale` field explaining why the change was made, but the dialog had no UI surface to show it.

## Task

- Extend the `InstructionRevision` type to include `'campaign'` as a valid `source` value and an optional `context` field carrying `campaign_id`, `attempt_id`, and `rationale`.
- Show a properly localised badge label ("Campaign retrospective" / "캠페인 회고") for campaign-sourced revisions.
- When a campaign revision is selected in the history preview, display its rationale (if present) above the instruction text.
- Add a test covering the new behaviour (label rendering + rationale display + rationale absence for non-campaign revisions).
- Keep the badge safe against unknown future `source` values (fall through to raw string rather than crash).

## Action

- `src/hooks/useAgents.ts` — Added `'campaign'` to the `source` union of `InstructionRevision` and added the optional `context?: { campaign_id?: string; attempt_id?: string; rationale?: string } | null` field.
- `src/components/agent-settings/InstructionHistoryDialog.tsx` — Introduced a `SOURCE_KEYS` lookup map from source string to `MessageKey`; the badge now resolves via that map and falls back to the raw source string for unknown values (replacing the previously unguarded template-literal `t()` call). Added a conditional `<p>` block under `instruction-history-rationale` test-id that renders `'admin.instructionHistory.rationale'` when `chosen.context?.rationale` is truthy.
- `src/i18n/catalogs/admin.ts` — Added `'admin.instructionHistory.source.campaign'` ("Campaign retrospective" / "캠페인 회고") and `'admin.instructionHistory.rationale'` ("Why: {{rationale}}" / "이유: {{rationale}}") to both EN and KO catalogs.
- `src/components/agent-settings/ManifestPanel.test.tsx` — Added test `'labels a campaign retrospective version and shows its reason [AGT-11]'` that renders a history with a campaign-sourced revision carrying a rationale, verifies the badge text and rationale paragraph, then clicks a non-campaign revision and verifies the rationale paragraph disappears.

## Decisions

- **`SOURCE_KEYS` map over direct template-literal `t()` call**: The prior code used `` t(`admin.instructionHistory.source.${revision.source}`) `` which would pass an unknown key to the translation function if a new source type appeared. A static lookup map makes valid keys explicit to TypeScript and the i18n type-checker, and makes the fallback (raw string) clear and intentional.
- **Optional `context` field rather than top-level `rationale`**: Grouping campaign-specific metadata under `context` keeps the `InstructionRevision` interface flat for the common case and bundles related fields (`campaign_id`, `attempt_id`, `rationale`) together for consumers that need more than just the reason string. An alternative of adding `rationale?: string` at the top level was considered but rejected as it would conflate campaign-specific data with the core revision shape.
- **Rationale shown only when `context?.rationale` is truthy**: Avoids rendering an empty "Why: " line for campaign revisions that were written before the rationale field was populated.

## Result

- Campaign-sourced instruction revisions now show "Campaign retrospective" (EN) / "캠페인 회고" (KO) as their source badge instead of a raw string.
- Selecting such a revision in the history preview shows the retrospective's rationale prefixed with "Why: " / "이유: ".
- Unknown future source values fall back gracefully to their raw string rather than a translation miss.
- New test [AGT-11] covers all three assertions and passes.
