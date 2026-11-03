# feat(auth): personal API keys for calling the API from outside the browser (#988)

- Commit: `c2dcdb5` (c2dcdb53b5b9095842b3094c05a6a27edf27eafb)
- Author: Changyong Um
- Date: 2026-11-03T11:52:06+09:00
- PR: — (issue #988)

## Situation

Scripts, external services and LLM clients needed a way to call the Anygarden REST/WebSocket API on behalf of a real user, but no long-lived personal credential existed. Session JWTs expire in 24 hours and require a browser-based login flow; agent tokens (`agt_`) act as an agent identity, not as the person who owns the agent. Any automation that needed user-level access was forced to re-authenticate on a schedule or embed a short-lived token.

## Task

Introduce personal API keys (`agu_` prefix) that:
- Are stored safely (argon2 hash + 12-char lookup hint, never plaintext) — same shape as agent/machine tokens.
- Carry an explicit scope (`read` / `write` / `admin`) enforced in the identity layer.
- Support optional expiry, instant revocation, and a coarse `last_used_at` hint.
- Are manageable through a session-gated REST endpoint (`/api/v1/auth/api-keys`) and a "Personal settings > API keys" UI dialog.
- Are rate-limited so repeated wrong keys from one IP get a 429 (shared `FailureLimiter` with the login flow).
- Satisfy tracked issues AUTH-11..AUTH-15.

## Action

- **`packages/cluster/anygarden/auth/api_key.py`** (new): key generation (`secrets.token_urlsafe(32)` with `agu_` prefix), argon2 hashing, `scope_allows` rank comparison (`read < write < admin`).
- **`packages/cluster/anygarden/auth/api_key_routes.py`** (new): FastAPI router at `/api/v1/auth/api-keys` — `GET` (list active keys), `POST` (issue, plaintext returned once), `DELETE /{key_id}` (revoke). Enforces `MAX_ACTIVE_API_KEYS = 20` per user; admin-scoped keys require the issuer to be an admin in the DB.
- **`packages/cluster/anygarden/auth/dependencies.py`**: `get_identity` now checks for the `agu_` prefix before JWT handling and delegates to `_identity_from_api_key`. That resolver looks up candidates by `lookup_hint`, verifies the argon2 hash, checks expiry, and builds a `UserClaims` with `is_admin` set only for `admin`-scoped keys. `Identity` gains `api_key_scope` and `api_key_id` fields. `last_used_at` is updated at most once per 60 s to avoid turning read-only polling into DB churn.
- **`packages/cluster/anygarden/auth/limiter.py`** (new): `FailureLimiter` — in-process sliding-window counter; shared with the login rate-limit path.
- **`packages/cluster/anygarden/auth/routes.py`**: `LoginFailureLimiter` now subclasses the shared `FailureLimiter` (behaviour unchanged).
- **`packages/cluster/anygarden/db/models.py`**: new `UserApiKey` ORM model (`user_api_keys` table) with indexes on `lookup_hint` and `user_id`.
- **`packages/cluster/anygarden/db/migrations/versions/103_user_api_keys.py`** (new): Alembic migration creating the table.
- **`packages/cluster/anygarden/dependencies.py`**: `get_current_identity` holds a key to its scope by HTTP method (`read` = GET/HEAD/OPTIONS, other methods need `write`) and returns 429 after 20 wrong keys per client address in 15 minutes; new `get_session_identity` rejects keys so key management stays session-only.
- **`packages/cluster/anygarden/ws/handler.py`**: keys already authenticate WS through `get_identity`; `_identity_may_send_frame` answers a `read` key's send/typing/lifecycle frames with an error frame instead of acting on them.
- **`packages/cluster/frontend/src/components/ApiKeysDialog.tsx`** (new, 273 lines): React dialog for listing, issuing and revoking keys; one-time plaintext reveal.
- **`packages/cluster/frontend/src/components/ApiKeysDialog.test.tsx`** (new, 104 lines): tests for the dialog.
- **`packages/cluster/frontend/src/components/SidebarPreferencesMenu.tsx`**: "API keys" entry added.
- **`packages/cluster/frontend/src/i18n/catalogs/apiKeys.ts`** (new): i18n strings.
- **`packages/cluster/tests/test_user_api_keys.py`** (new, 306 lines): end-to-end tests covering create/list/revoke, scope enforcement, expiry, rate limiting, and the 20-key cap.
- **`docs/requirements.md`**, **`packages/cluster/docs/api.md`**: documented the new endpoints and key shape.

## Decisions

- **argon2 hash + lookup hint, not bcrypt or HMAC**: matches the existing `AgentToken` / `MachineToken` pattern already in `models.py`. Switching to HMAC-SHA256 (faster lookup, no per-candidate hash comparison) was considered but rejected because argon2 is already the project standard for secrets at rest and the candidate set per hint is at most a handful of keys.
- **Scopes enforced by HTTP method in one dependency, not per route** (chosen with the user over per-route scopes and no scopes): a flat `read < write < admin` rank replaces per-endpoint scope checks. The alternative — declaring required scopes per-route as FastAPI dependencies — would have added noise to every route and risked omissions. Keeping `scope_allows` in one place means all existing `claims.is_admin` checks remain unaware of keys; only the `is_admin` value in `UserClaims` is adjusted at key-resolve time.
- **Session-only key management**: `get_session_identity` (not `get_identity`) gates `/api/v1/auth/api-keys` so a key cannot create or revoke other keys. This prevents a compromised key from bootstrapping further access.
- **In-process `FailureLimiter`**: adequate for a single-process deployment. A multi-replica setup would need Redis or similar. That caveat is noted in `limiter.py`; revisit when horizontal scaling is introduced.
- **60 s `last_used_at` resolution**: a pure UX hint; writing on every call would add a DB write to every polling request. If precise audit logging becomes a requirement, a separate event log should be added rather than tightening this resolution.

## Result

- Scripts and LLM clients can authenticate against REST and WebSocket endpoints using a long-lived `agu_*` key scoped to `read`, `write`, or `admin`.
- Keys are manageable from the browser UI (Personal settings > API keys).
- `tests/test_user_api_keys.py` (16 tests) and `ApiKeysDialog.test.tsx` (4 tests) cover the lifecycle; full suite 4659 passed / 7 skipped, vitest 1202 passed. Hardcoded head revision in `test_migrations.py`/`test_federation_trust.py` bumped to 103.
- Follow-ups: #989 (REST/WS guide), #990 (user MCP endpoint). Wrong-key limiting covers REST only, not WS handshakes.
- Alembic migration `103_user_api_keys` creates the table on next deploy.
- AUTH-11..AUTH-15 added to `docs/requirements.md` and linked to tests.
