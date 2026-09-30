# fix(search): escape snippet markup, name mentions, layer focus outline (#774)

- Commit: `8ac41c8` (8ac41c8ac21267a38f194ad02f3b806b1decc855)
- Author: Changyong Um
- Date: 2026-10-01T08:55:57+09:00
- PR: #774

## Situation

While visually verifying the #774 audit fixes, the search dialog showed raw `<@user:uuid>` tokens in result snippets. Tracing it showed a worse problem.
- `GET /api/v1/search` built snippets with FTS5 `highlight(messages_fts, 0, '<mark>', '</mark>')` over raw message text.
- `SearchDialog` rendered that snippet with `dangerouslySetInnerHTML`.
- Any message containing HTML (e.g. `<img onerror>`) was therefore executed in the searching user's browser. That is a stored XSS path.

Two smaller follow-ups from the audit were also pending:
- The global `:focus-visible` outline was unlayered, so it beat Tailwind's layered `outline-none` utilities and double-outlined components that draw their own ring.
- The tracked `.design-sync/conventions.md` still described the retired Notion palette.

## Task

- Make the snippet safe HTML without breaking the API contract (`snippet` stays an HTML string with `<mark>`).
- Show mention tokens as readable names, without exposing names of rooms the caller cannot access.
- Make the dark-theme highlight readable.
- Let component-level `outline-none` take effect while keeping the global fallback.
- Update the design-sync conventions doc.

## Action

- `packages/cluster/anygarden/api/v1/search.py`
  - Both queries use `highlight(messages_fts, 0, char(2), char(3))`.
  - `_render_snippet` resolves mention tokens, then HTML-escapes the text, then turns the `\x02`/`\x03` markers into `<mark>`/`</mark>`. Markers that land inside a mention token are stripped first, so the token still resolves.
  - `_mention_names` batch-loads participant names (`Agent.name` → `User.display_name` → `User.email`) and room names, limiting room names to `allowed_room_ids`.
- Tests
  - `packages/cluster/tests/test_search_snippet.py` (new): escaping, literal `<mark>` in a message, mention resolution, unknown ids, escaped names, highlight inside a token.
  - `packages/cluster/tests/test_message_thread_regression.py`: end-to-end test posting `<img onerror>` + mention + needle and asserting the snippet is escaped, mention-free and highlighted.
- `packages/cluster/frontend/src/components/SearchDialog.tsx`: `<mark>` styled with `--color-brand-tint-bg`/`-text`. The input drops the `outline-none!` override, which is no longer needed.
- `packages/cluster/frontend/src/index.css`: `:focus-visible` outline wrapped in `@layer base`.
- `.design-sync/conventions.md`: intro and colour/type token lines describe the teal system, `on-brand`, `brand-text`, `overlay` and `text-body`.
- The local, gitignored `CLAUDE.md` design summary was also rewritten; it is not part of this commit.

## Decisions

- **Where to fix the XSS.**
  - Options:
    - (a) Sanitize in the frontend (DOMPurify, or render text nodes by splitting on `<mark>`).
    - (b) Escape on the server and keep the HTML contract.
    - (c) Change the API to return structured segments.
  - (b) was chosen. It fixes every consumer of the endpoint at the source, needs no new dependency, and keeps the response shape.
  - (a) alone was rejected because splitting on literal `<mark>` cannot tell highlighter markup from user-typed `<mark>`.
  - (c) was deferred as a larger API change.
- **Control-character markers (`char(2)`/`char(3)`)** instead of `<mark>`. The markers cannot be confused with user text, so escaping can run safely before the markup is restored. Revisit if message content is ever allowed to contain these control characters.
- **Mention names on the server.** The dialog searches across rooms and has no participant list for other rooms, so it cannot resolve names itself.
  - Room names are only resolved for rooms the caller can see. Other mentioned rooms render as `#?`, so private room names do not leak.
  - User names are resolved for any participant id in a message the caller can already read.
- **Focus outline.** Moving the rule into `@layer base` was chosen over adding `!important` overrides per component. It restores the intended cascade everywhere.
  - Checked beforehand: every remaining `outline-none` either pairs with a ring, or has an alternative indicator (the MessageInput wrapper `focus-within` ring, the RailResizeHandle `group-focus-visible` bar, the SearchDialog row border).

## Result

- Search snippets are HTML-escaped. `<mark>` is the only markup in them, and mentions read `@name`/`#room`.
- Backend: 2681 passed, 2 skipped (full cluster suite, `-n logical`). The 6 new snippet unit tests and the endpoint regression test pass.
- Frontend: `npm run build` passes, vitest 794 passed, Playwright e2e 41 passed.
- Checked in the browser: in dark theme the highlight uses the brand tint, and a header icon button shows a single 2px focus outline with no ring overlap.
- Not verified in the running app: the running dev backend is the main checkout, so mention name rendering is covered by tests only.
