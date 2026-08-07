# Actual browser UI screenshot evidence

The Playwright browser captures in `e2e/ui-evidence.spec.ts` use the real
React application, routes, and controls. Their isolated API responses make
the browser journey reproducible; no image is generated or composited, and no
provider, machine, or external workspace is contacted.

- Phase 3: create a task from the room context rail, claim it, then capture
  the in-progress UI.
- Phase 5: render an active scoped-write workspace attachment and capture the
  warning banner in the room UI.
- Phase 2: **not captured**. The current frontend renders a flat message feed:
  `parent_message_id` and `root_message_id` are transported by the WebSocket
  type but are not rendered as a user-visible thread hierarchy or thread
  detail view. Capturing ordinary message bubbles as a "thread UI" would be
  misleading, so no Phase 2 image is produced until such a surface exists.

Run from `packages/cluster/frontend`:

```sh
npm run test:e2e -- e2e/ui-evidence.spec.ts
```

Playwright writes the actual PNG files under `test-results/`; their exact
paths are recorded in the task handoff after the test run.
