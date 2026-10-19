# fix(agent): name the action in auth failure notices instead of asking an admin (#902)

- Commit: `e9441ce` (e9441ce3c096a764241f560d8e8f894fa5aa3d12)
- Author: Changyong Um
- Date: 2026-10-19T15:12:59+09:00
- PR: #902

## Situation

Auth-failure notices in the agent runtime told users to "ask an admin to check the login or API key settings." On a fresh Anygarden node, the first user is automatically the admin, so that instruction sent them in circles with no actionable path forward. The notices were actor-directed rather than action-directed, making them unhelpful for self-serve onboarding.

## Task

Rewrite every auth-failure notice string so it names the concrete step to take rather than the role of the person who should take it. Additionally, differentiate the notice for Codex-CLI agents, which require running `codex login` on the agent's host machine — a step the generic model-connection URL cannot cover.

Constraints:
- No changes to error classification logic or error codes.
- Korean UI strings only (the notices are user-facing Korean text).
- New per-engine dispatch must be backward-compatible: unknown engines fall back to the generic notice.
- Existing parametrize test must be updated to reflect the new strings and engine differentiation.

## Action

**`packages/cluster/anygarden/agent/runtime/handler_wrapper.py`**

- Lines 275–278: Replaced four notice constants with action-named equivalents:
  - `_AUTH_NOTICE` → points to "에이전트 설정의 모델 연결" (agent settings → model connection).
  - `_CODEX_AUTH_NOTICE` (new): tells the reader to run `codex login` on the agent's computer.
  - `_PI_PROVIDER_NOTICE`, `_PI_NATIVE_AUTH_NOTICE`, `_PI_NATIVE_CONFIG_NOTICE` → each now references "에이전트 설정의 모델 연결" instead of "관리자에게 … 요청".
- Line 280: Added `_ENGINE_AUTH_NOTICES = {"codex-cli": _CODEX_AUTH_NOTICE}` — a per-engine override table keyed on engine name.
- Lines 320–323: Added `RoomHandlerSupervisor._failure_notice(error)` method; for `ENGINE_AUTH_ERROR` it looks up `_ENGINE_AUTH_NOTICES` by `self._engine`, falling back to `_AUTH_NOTICE`; all other errors still hit the old `_FAILURE_NOTICES` dict.
- Line 780: Changed `_FAILURE_NOTICES.get(error, _FAILED_NOTICE)` call-site to `self._failure_notice(error)`.

**`packages/cluster/tests/agent/test_handler_supervisor.py`**

- Lines 248–256: Added `engine` as a parametrize axis; added `("codex-cli", "ENGINE_AUTH_ERROR", "codex login")` case to verify the Codex-specific path; relabeled other cases to `"pi-cli"` engine for explicitness.
- Line 274: Updated `RoomHandlerSupervisor` construction to pass `engine_name=engine`.
- Lines 275–277: Added assertion `assert "관리자" not in client.sends[0][1]` to enforce the no-admin-deflection rule across all notice codes.

## Decisions

**Option A (chosen):** Add a per-engine lookup table (`_ENGINE_AUTH_NOTICES`) and a dispatch method, leaving the global `_FAILURE_NOTICES` table for non-engine-specific errors. This keeps the existing error classification path unchanged and makes the Codex-specific case explicit and easy to extend.

**Option B:** Add a separate `if engine == "codex-cli"` branch directly inside the send-message call site. Rejected — it would scatter engine logic away from the notice constants and make future additions harder to spot.

**Option C:** Pass the engine into every `_FAILURE_NOTICES` lookup by replacing the dict with a function. Rejected as over-engineering for a single engine exception; the lookup table is sufficient.

**Tipping factor:** The diff needed to stay small and the test update needed to be mechanical; the lookup-table approach required the fewest structural changes.

**Assumption to revisit:** If a third engine type ever needs its own `ENGINE_AUTH_ERROR` wording, the lookup table extends trivially. If the number of per-engine overrides grows beyond ~5, consider a per-engine notice dataclass instead.

## Result

- All five auth/provider error codes now produce notices that name the specific action ("모델 연결 확인", "codex login 실행") instead of delegating to an unnamed admin.
- `codex-cli` `ENGINE_AUTH_ERROR` produces the `codex login` notice; all other engines produce the generic model-connection notice.
- The `"관리자"` string no longer appears in any failure notice sent to room participants.
- Tests updated to cover both engine paths for `ENGINE_AUTH_ERROR` and assert the no-admin-deflection invariant.
- No behavior changes outside notice strings; error codes and activity metadata are unaffected.
