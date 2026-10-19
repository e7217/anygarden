# feat(auth): open the login page on Create account for a fresh node (#902)

- Commit: `1020f0f` (1020f0f089763fdc1e2efecaea512aa038000141)
- Author: Changyong Um
- Date: 2026-10-19T15:18:38+09:00
- PR: #902

## Situation

A brand-new Doorae node has no user accounts yet. When a first-time visitor opened the login page, the form defaulted to the "Log in" tab — which requires credentials that do not exist. There was no way to proceed without manually clicking "Create account". The registration endpoint already allowed anyone to sign up on an empty node; the only missing piece was surfacing that fact to the frontend.

## Task

- Add a public API endpoint that tells the UI whether the node is still waiting for its first admin account.
- Make the `LoginForm` component query that endpoint on mount and switch to the "Create account" tab automatically when the node is fresh.
- Add a requirement marker (`AUTH-10`) to `docs/requirements.md` covering the new endpoint and the tab-open behavior.
- Add backend and frontend tests for the new behavior.

## Action

- `packages/cluster/anygarden/auth/routes.py`: Added `SetupStatusResponse` Pydantic model and a new `GET /auth/setup` route. The handler runs a single SQL `EXISTS` query against the `User` table; it returns `{"needs_first_account": true}` when no user row exists and `false` otherwise. The route is unauthenticated.
- `packages/cluster/frontend/src/components/LoginForm.tsx`: Switched the `<Tabs>` component from uncontrolled (`defaultValue`) to controlled (`value` + `onValueChange`). Added a `useEffect` that calls `/api/v1/auth/setup` on mount and sets the tab to `'register'` if `needs_first_account` is `true`. The effect uses an `active` flag to avoid state updates on unmounted components, and silently swallows any network errors.
- `packages/cluster/frontend/src/components/LoginForm.test.tsx`: New Vitest tests verifying that the tab opens on "Create account" when the mock returns `needs_first_account: true`, and stays on "Log in" otherwise.
- `packages/cluster/tests/test_auth_routes.py`: New pytest tests verifying `GET /auth/setup` returns `200` with the correct boolean in both the empty-DB and the populated-DB cases.
- `docs/requirements.md`: Added `AUTH-10` row specifying the contract for both the API response and the UI tab behavior, with pytest and vitest as the enforcing test suites.
- `worklogs/902-print-where-to-open-the-ui-once-the-node-is-up-902.md`: A companion worklog bundled in this commit (prior step).

## Decisions

- **Public endpoint vs. requiring no token**: The endpoint reveals only whether *any* account exists. On an empty node anyone who can reach the server can already register without any credential — so exposing `needs_first_account: true` adds no attack surface. Requiring authentication would create a chicken-and-egg problem: the user can't log in to find out they need to register.
- **Frontend poll vs. backend redirect**: Querying from the React mount lets the login page remain a static asset served from any CDN; a server-side redirect would couple the SPA router to SSR logic. The fetch cost is one tiny JSON response and fires only once per page load.
- **Controlled vs. uncontrolled `<Tabs>`**: Switching to `value`/`onValueChange` was necessary to let the effect override the initial tab. An alternative of momentarily re-mounting with a changed `key` was rejected as unnecessarily disruptive to the component tree.
- **Assumption**: If multi-tenancy (per-project user namespaces) is ever introduced, the `needs_first_account` semantics will need to be re-evaluated — the current check is node-wide.

## Result

Fresh-node visitors now land on the "Create account" tab automatically, eliminating the confusion of facing a login form with nothing to log in with. The `GET /auth/setup` endpoint is covered by pytest; the tab-open behavior is covered by Vitest. `AUTH-10` is tracked in `docs/requirements.md` and will be enforced by CI's requirement-coverage job.
