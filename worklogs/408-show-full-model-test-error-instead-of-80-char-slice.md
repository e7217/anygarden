# fix(llm-gateway): show full model test error instead of 80-char slice (#408)

- Commit: `6964457` (6964457088773edfd9d0ff05e8c4b18852734722)
- Author: Changyong Um
- Date: 2026-06-02T14:35:37+09:00
- PR: #408

## Situation

The LLM Gateway admin UI lets an operator click **Test** on a model card to probe upstream
connectivity. When the test failed, the card showed `✗ Failed` plus the error message — but the
message was cut to 80 characters by `error.slice(0, 80)`. litellm/upstream errors are frequently
longer than that (URLs, auth detail, multi-line proxy errors), so the operator could only read the
full text by opening browser DevTools and inspecting the `/test` response JSON.

## Task

- Render the full test error in the card without the 80-char cap.
- Keep it from breaking the card layout: long unbroken strings (URLs, tokens) must wrap, not
  overflow horizontally.
- Stay consistent with the existing gateway error styling.

## Action

- `packages/cluster/frontend/src/components/admin-llm-gateway/ModelsSection.tsx:211` — replaced the
  single truncating `<span>` with a `<div>` that keeps `✗ Failed (status_code)` inline and renders
  the full `testResult.error` in a separate `<p className="mt-1 whitespace-pre-wrap break-words
  font-mono text-[11px] text-red-900">`. Dropped `.slice(0, 80)` entirely.
- Verified via `npm run build` (tsc) — clean.

## Decisions

- **Where to show the full text** — inline-append (old shape) vs a dedicated block. Chose a
  separate `<p>` block: `whitespace-pre-wrap` only behaves usefully on a block-level line box, and
  separating the summary (`✗ Failed (code)`) from the detail keeps the at-a-glance status readable
  while the verbose message lives below.
- **Wrapping strategy** — `break-words` (overflow-wrap) chosen so long URLs/tokens wrap instead of
  forcing horizontal overflow; `whitespace-pre-wrap` preserves the newlines/spacing litellm emits
  in multi-line errors.
- **Styling** — matched `StatusSection.tsx`'s `last_error` presentation (`font-mono text-red-900`)
  rather than inventing a new treatment, so both gateway error surfaces read the same. red is an
  allowed danger signal per DESIGN.md §Status colors.
- **Rejected**: a tooltip/expand toggle (more code, hides the message by default) and leaving it to
  DevTools (the reported pain point). The user picked "full wrapped display" when asked.
- **Assumption**: errors are bounded in length (a few lines). If an upstream ever returns a huge
  payload as the error, the card could grow very tall — revisit with a max-height + scroll if that
  surfaces in practice.

## Result

- Test failures now show the complete error message wrapped inside the card; no DevTools detour.
- Frontend build/type-check passes. No behavior change to the test request itself or the success path.
