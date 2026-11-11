# feat(chat): attach local files a reply links and open them from chat (#1017)

- Commit: `f5350f0` (f5350f082716de64a2e9d62c019cef53930398f1)
- Author: Changyong Um
- Date: 2026-11-11T14:28:35+09:00
- PR: #1017 (issue)

## Situation

When an agent wrote a file outside `memory/outbox/<room>/` and linked it in its reply (e.g. `workspace/report.md`), the file was never uploaded. The chat showed a link to a path on the agent's machine that nobody could open. Only outbox files were attached (#854), and the chat only rewrote `memory/outbox/...` links (#964). The agent workspace page can download files, but it is admin-only and reads live from the machine, so it does not suit a conversation link.

## Task

- Attach the local files a reply links, at reply time, so any room member can download them, including after the machine goes offline.
- Never let a link expose files the engine sandbox could not reach, such as credentials, logs or the manifest. The supervisor runs outside that sandbox.
- User decisions: agents may attach any file type; HTML and SVG open in place; size follows the existing 20 MiB server cap.

## Action

- `agent/runtime/attachments.py`: `extract_local_links` (markdown links/autolinks outside code), `resolve_linked_file`, `upload_linked_files`, and a shared `_upload_one`. `upload_reply_attachments` can record path → artifact id.
  - Only files under `workspace/` and `memory/` qualify, excluding `memory/inbox` and `memory/shared`. A file is rejected if any component of its resolved path is hidden, and it must be a regular file.
  - Files over 20 MiB are reported as `too_large` before they are read.
- `agent/runtime/handler_wrapper.py`: `_attach_reply_files` merges outbox and linked files under the 10-attachment cap, reuses outbox uploads, and writes `metadata.linked_files` (`href` → `artifact_id`). `agent_root` is passed from `agent/integrations/codex_cli.py`.
- `rooms/artifacts.py` and `rooms/router.py`:
  - Uploads: `normalize_mime`; agent uploads skip `ALLOWED_MIME_TYPES` (person uploads keep it); only a missing type falls back to the filename.
  - Downloads: `download_headers` sends previewable types inline and the rest as attachment, always with `nosniff`. HTML, SVG and XML get `Content-Security-Policy: sandbox allow-scripts`.
- Frontend:
  - `lib/messageAttachments.ts`: `extractLinkedFiles`, `isLocalFileHref`, and `resolveLocalFileHref`, which generalises `resolveOutboxHref`.
  - `MarkdownContent.tsx`: a `linkedFiles` prop; a `urlTransform` that keeps `file:` links; unresolved local links render as text (`data-local-link-unresolved`).
  - `MessageAttachments.tsx`: `HtmlPreviewButton` opens HTML in an `<iframe sandbox="allow-scripts">`.
  - i18n keys in ko/en.
- `machine/spawner.py`: the agent instructions now say that linked files are attached, any type is allowed, and HTML reports should be one self-contained file.
- `docs/requirements.md`: added MSG-11.

## Decisions

- **Where bytes move.**
  - Options weighed: (a) the server relays the file live from the machine (workspace download API); (b) the agent side uploads at reply time; (c) a prompt rule alone.
  - Chosen: (b). It reuses the #854 upload path, works on remote machines, and freezes the file as conversation history.
  - Rejected: (a) needs a member-level permission model and breaks when the machine is offline or the file changes. (c) already failed, because the outbox rule existed and was ignored.
- **How a link maps to its file.** `metadata.linked_files` keeps the reply text untouched and tells apart files with the same basename. The server rebuilds `artifact` references from the DB, so a forged map can only point at this message's verified attachments. Rewriting the text, or matching by basename only, was rejected.
- **Which files qualify.**
  - The plan first allowed the whole agent root minus hidden paths. Inspecting a real agent root showed non-hidden `agent.log` and `manifest.json`, so the scope was narrowed to `workspace/` and `memory/`.
  - Revisit if engines start writing output elsewhere.
- **How HTML opens.**
  - The app authenticates with a Bearer token in localStorage. A `blob:` URL opened in a new tab would run in the app's origin, so HTML is shown in a sandboxed iframe without `allow-same-origin`.
  - Server CSP headers protect direct API consumers only.
  - The chat does not preview PDFs, so the server serves them inline without a sandbox; Chrome's PDF viewer does not run under CSP sandbox.

## Result

- Added `tests/agent/test_linked_reply_files.py` and new cases in `test_rooms_artifacts_endpoints.py`, `MarkdownContent.test.tsx` and `MessageAttachments.test.tsx`, tagged MSG-11. `test_materialize.py` was updated for the new instructions.
- Results: cluster pytest 4,716 passed; vitest 1,291 passed; Playwright e2e 111 passed; build OK.
- Still pending:
  - Live verification with a real engine.
  - Local paths quoted only as inline code are not attached.
  - Image markdown (`![](local.png)`) is attached, but the inline `<img>` is still broken.
  - The daemon outbox frame path (768 KiB) keeps its allow-list.
