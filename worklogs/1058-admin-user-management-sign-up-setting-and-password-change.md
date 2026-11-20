# feat(auth): admin user management, sign-up setting and password change (#1058)

- Commit: `ec999ff` (ec999ff73ecd8de9b1ac57fca2437bf49367367c)
- Author: Changyong Um
- Date: 2026-11-20T11:48:26+09:00
- PR: #1058 (issue)

## Situation

Login and registration existed, but nothing managed accounts afterwards. Anyone who could reach a node could register and admins could not close sign-up. Admins had no way to list users, grant or remove admin rights, suspend an account or reset a forgotten password short of editing the DB. Users could not change their own password, and a leaked 24h session JWT could not be cut off. The 2026-07 gap audit had already flagged "account deactivation + admin user management API" as a top security gap.

## Task

- Admin API and UI to list/search accounts, create accounts, grant/remove admin, suspend/restore, and reset passwords.
- A runtime switch for open sign-up. It stays open by default so existing nodes behave as before, and the first account can always register.
- Suspended accounts must be refused at login, for existing sessions, and for API keys.
- Self-service password change. A password change or reset must end earlier sessions.
- Keep history intact: messages, rooms and tasks reference `users` rows, so no hard delete.

## Action

- Model + migration `108_user_management`: `users.disabled_at`, `last_login_at`, `credentials_changed_at`; new key/value `server_settings` table (`anygarden/db/models.py`, `db/migrations/versions/108_user_management.py`).
- `anygarden/auth/settings.py`: registration-open read/write, password length bounds (8–256).
- `anygarden/auth/jwt.py`: user tokens carry `iat_ms`; `UserClaims.issued_at_ms` falls back to `iat*1000` for older tokens.
- `anygarden/auth/dependencies.py`: on each request the per-request user lookup also rejects suspended accounts (`Account disabled`) and tokens issued before `credentials_changed_at`. API keys of suspended users are refused.
- `anygarden/auth/routes.py`: register honours the setting (403 when closed, except for the first account), validates email/password, and records `last_login_at`. Login refuses suspended accounts (403) and records `last_login_at`. New `POST /auth/password` (requires the current password, refuses API keys, returns a fresh token). `/auth/setup` reports `registration_open`. `dev-token` skips suspended admins.
- `anygarden/api/v1/users.py` (new, registered in `app.py`): `GET/POST /api/v1/admin/users`, `PATCH /users/{id}` (is_admin/disabled; refuses changes to the caller's own account), `POST /users/{id}/password`, `GET/PUT /api/v1/admin/account-settings`.
- Frontend: `AdminUsers.tsx` + `pages/AdminUsersPage.tsx` with a route at `/admin/users` and a "Users" link in the admin menu. Also `ChangePasswordDialog.tsx` in Personal settings, plus `LoginForm.tsx` changes: hides Create account when sign-up is closed, shows a message for suspended accounts, and requires 8+ characters. Copy lives in `i18n/catalogs/users.ts` (en/ko).
- Requirements AUTH-17..21 in `docs/requirements.md`. Tests: `tests/test_admin_users.py`, `AdminUsers.test.tsx`, and new LoginForm cases. Existing tests updated for 8-char passwords and the new migration head.

## Decisions

- **Suspend instead of delete.** Many tables reference `users`, and deleting would orphan or cascade conversation history. Suspension is reversible and still cuts off access.
- **Revocation via `credentials_changed_at` + per-request check.** The resolver already loads the user row on every request (#782), so the extra columns cost nothing. A token blocklist or token-version claim was not needed. JWT `iat` is whole seconds, so a session issued in the same second as a change would survive and a fresh token could be rejected. A millisecond `iat_ms` claim fixes both.
- **Key/value `server_settings` table instead of config file.** Admins need to flip sign-up at runtime from the UI, and later settings will need no migration. The default stays open so upgraded nodes keep their old behaviour.
- **Last-active-admin guard was dropped.** Only an active admin can call the endpoints, and that admin cannot demote or suspend themselves, so another active admin always remains. The separate count check could never fire through the API. A concurrent mutual demotion is still theoretically possible. Revisit if that matters.
- **Scope excluded:** email verification and reset-by-email (no mail transport), OAuth/SSO, and account deletion.

## Result

- Admins manage accounts from Settings → Users. Sign-up can be closed. Suspended users are locked out at once. Users can change their password, which signs out their other sessions.
- Verified: cluster pytest 4757 passed, plus the 6 project tests re-run after their password fix. All 177 vitest files (1367 tests) and the frontend build pass. A live browser run on an isolated node covered first signup → add user → close sign-up → suspend → login refused → password change, plus the 375px layout.
- Pending: PR review/merge; existing nodes get migration 108 automatically on start.
