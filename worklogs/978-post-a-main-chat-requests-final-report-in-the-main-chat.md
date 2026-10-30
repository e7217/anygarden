# fix(projects): post a main-chat request's final report in the main chat (#978)

- Commit: `53b322b` (53b322bb4c89a82a409f30d8a129b4c6cd396d10)
- Author: Changyong Um
- Date: 2026-10-30T11:49:04+09:00
- PR: #978

## Situation

When a user posted a project execution request in the main chat (top-level, not inside a thread), the final report was always threaded under that request message rather than posted to the main chat. This meant the user had to open the thread to see the result of their request, even though they made the request at the top level. Campaign-cycle reports were also affected but those should legitimately stay threaded because the campaign goal report already surfaces in the main chat (per #964).

## Task

- Change `complete_project_execution` so that the final report is posted to the main chat when the originating request was a top-level (non-threaded) message.
- Preserve the existing behaviour for threaded requests: reply in the same thread.
- Preserve the campaign-cycle exception: cycle reports must stay threaded (the campaign goal report reaches the main chat via #964).
- Keep the child-result notice in the thread in all cases (working evidence stays in the thread).
- Add tests covering both the main-chat and thread-request paths.

## Action

- `packages/cluster/anygarden/project_executions/service.py:2124–2128` — `complete_project_execution` now computes `thread_root_id` as:
  - `source.root_message_id` if the source message was already in a thread (preserves thread reply), else
  - `source.id` if the execution belongs to a campaign (cycle report stays threaded), else
  - `None` (main-chat request → post to main chat).
- `packages/cluster/tests/test_project_execution_flow.py:250–289` — `Harness.user_request` and `begin_and_delegate` extended with a `thread_root_id` parameter so tests can simulate both main-chat and threaded requests.
- `packages/cluster/tests/test_project_execution_flow.py:947–998` — Added two new parametrised tests:
  - `test_final_report_for_main_chat_request_lands_in_main_chat` — asserts `report.root_message_id is None` and that the report appears in the main-chat history endpoint.
  - `test_final_report_for_thread_request_stays_in_that_thread` — asserts `report.root_message_id == root_id`.
- `packages/cluster/tests/test_campaigns_execution.py:304–306` — Added assertion that a cycle report's `root_message_id` equals the source message id (stays threaded).

## Decisions

- **Keep child-result notice in the thread**: the notice is working evidence produced during execution; it belongs with the request thread regardless of where the final report lands.
- **Campaign-cycle exception via `root.campaign_id` check**: two options were weighed:
  1. A dedicated flag on the execution or turn.
  2. Checking `root.campaign_id` inline at report time.
  Option 2 was chosen because `campaign_id` is already on the execution root and accurately distinguishes campaign executions from standalone ones without adding new model fields. The comment in the code (`#964`) ties it back to the earlier decision.
- **`source.root_message_id or (source.id if root.campaign_id else None)`**: the expression is intentionally kept as a single `thread_root_id` argument rather than splitting it into separate code paths, to avoid duplicating the `_queue_message` call. Assumption: `root.campaign_id` reliably identifies campaign executions; if campaign-less executions ever need threaded replies, this condition must be revisited.

## Result

- Final reports for main-chat requests now appear directly in the main chat, immediately visible without opening any thread.
- Threaded requests continue to be answered in their thread.
- Campaign cycle reports remain threaded as before.
- Two new regression tests (`EXE-08`) cover both paths; the existing campaign test is extended with a threading assertion.
