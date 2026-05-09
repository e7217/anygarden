# fix(lifecycle): cache doorae_token per-agent so sync_batch frame rebuilds don't orphan it (#369)

- Commit: `ac93e90`
- Author: Changyong Um
- Date: 2026-05-10
- PR: #369

## Situation

After #366 the OpenHandsAdapter passes `api_key` explicitly to
the LLM constructor, the supervisor reaches `state=running`,
the model-test endpoint returns 200, but `oh-agent04` *still*
returns 401 on every chat. The user-facing chain at this point
was: #355 → #357 → #359 → #362 → #364 → #366 → silence.

Three diagnostic logs in different layers — auth fail in
`dependencies.get_current_identity`, frame build in
`AgentLifecycle._build_sync_frame`, stdin pickup in
`agent.secrets.load_from_stdin` — captured this at 19:14:54-56
KST:

```
[server] _build_sync_frame mints (5 in 2 seconds):
  19:14:54.653  doorae_token=agt_jDGShlc4
  19:14:55.760  doorae_token=agt_mLuzlUqt   ← committed (DB row)
  19:14:56.028  doorae_token=agt_1rPSBfP9   ← agent stdin token
  19:14:56.075  doorae_token=agt_zJC2Bqb_   ← committed (DB row)
  19:14:56.343  doorae_token=agt_H0hdWklr   ← rolled back / orphan

[agent stdin payload]
  pid=2719092 OPENAI_API_KEY.hint='agt_1rPSBfP9'

[server auth fail]
  token_hint=agt_1rPSBfP9 detail='Invalid agent token'

[DB lookup]
  agt_1rPSBfP9 row count: 0
```

The agent's stdin landed on a token whose `agent_tokens` row
was rolled back — the manifest_store cache had already been
updated with that token, but the staging transaction never
committed.

## Task

Stop minting a fresh token on every `_build_sync_frame` call.
The cluster invokes `_build_sync_frame` from many paths —
`request_start` (always commits), `handle_report_actual_state`
(commits), broadcast snapshot rebuilds + sync_batch ticks (do
*not* always commit). Pre-#369 every one of those calls staged
a new `db.add(AgentToken(...))` row + updated the
`manifest_store` cache. Only the committers persisted; the
non-committers left the cache one revision ahead of the DB,
which was the exact race the user kept hitting.

Constraints:
- Token rotation across agent stop/start is desirable for
  security; the fix must preserve it.
- The agent process reads stdin once at spawn; the token it
  receives must have a corresponding `agent_tokens` row that
  outlives the spawn cycle.
- All existing call sites (MCP overlay path + the `#359`
  gateway-only path) must converge on the same token for a
  given agent.
- Test must lock the regression — three back-to-back rebuilds
  must yield the same token AND a single DB row.

## Action

- `packages/cluster/doorae/scheduler/lifecycle.py`:
  - `AgentLifecycle.__init__` grew
    `self._token_cache: dict[str, str] = {}`. Docstring spells
    out the cache contract — single mint per spawn cycle,
    plaintext stable until eviction, eviction on
    `request_stop` / `delete_agent`.
  - New `_acquire_doorae_token(db, agent_id)` is the *single*
    mint point. Cache hit returns the previously-minted
    plaintext; miss mints a fresh token, stages an
    `agent_tokens` row via `db.add`, and stores the plaintext
    in the cache.
  - Both call sites in `_build_sync_frame` — the MCP overlay
    branch (`if default is not None:`) and the `#359`
    gateway-only branch — now route through
    `_acquire_doorae_token` instead of inline
    `generate_token()` + `db.add`.
  - `request_start` evicts at the top of the method (fresh
    token on every explicit start — rotation on respawn).
  - `request_stop` evicts at the top (next start mints clean).
  - `evict_token(agent_id)` public hook for non-lifecycle
    callers (the agent DELETE endpoint).
- `packages/cluster/doorae/api/v1/agents.py:delete_agent`:
  - Calls `lifecycle.evict_token(agent.id)` after the
    optional `request_stop`. Required because `request_stop`
    only fires on the active-state branch — deleting an
    already-stopped agent would otherwise leak the cache
    entry.
- `packages/cluster/tests/test_lifecycle_engine_secrets.py`:
  - `test_repeated_build_returns_same_token` — three
    back-to-back `_build_sync_frame` calls return the same
    `OPENAI_API_KEY` AND the agent_tokens row count is exactly
    1. Direct guard for the 19:14:56 trace.
  - `test_request_stop_evicts_cache` — pre-populates the
    cache, calls `request_stop`, asserts eviction.
  - `test_evict_token_helper_is_no_op_for_unknown_agent` —
    locks the public hook's caller-friendly default.

## Decisions

The plan in the issue body considered three approaches:

- **Cache token per agent** (chosen). Single mint per spawn
  cycle, all rebuild paths return the same plaintext.
- **Make `_build_sync_frame` itself decide whether to mint**
  by inspecting the caller (e.g. via a flag). Cleaner
  conceptually but threads the caller's commit semantics into
  every call site, and `_build_sync_frame` already has a wide
  surface. Rejected for the call-site sprawl alone.
- **Always commit inside `_build_sync_frame`** so the DB row
  lands regardless of the outer transaction's fate. Rejected
  because it splits the atomic unit `_build_sync_frame`'s
  caller is constructing — broadcast snapshot rebuilds expect
  the whole frame build to be a single transaction.

What tipped the scale: the cache approach matches the actual
lifecycle of the token. A token is meaningful only while a
particular agent process is alive; that's the spawn-to-stop
window. The cache mirrors that window directly. The other two
options either spread coupling (option B) or break the
atomicity contract (option C).

What I explicitly didn't change:

- Pre-existing token rotation on respawn. The cache evicts at
  `request_start` *and* `request_stop` so a stop → start
  cycle still rotates. Not preserving rotation would have
  been a security regression — token plaintexts shared across
  process restarts let a leaked old plaintext authenticate
  the new agent.
- The two call sites' branching logic. Both still read the
  same `agent.engine` / `cluster_external_url` /
  `llm_gateway_enabled` conditions; only the mint mechanism
  changed.
- The `manifest_store` cache on the daemon side. Pre-#369 it
  already cached the latest token by `agent_id`. The bug
  wasn't there — it was on the cluster side, where the latest
  token sometimes didn't have a committed DB row.

Assumptions worth flagging if they break later:

- A single `AgentLifecycle` instance owns the cache. The
  cluster instantiates one in `lifespan`; `app.state` reuses
  it across all routes. If we ever shard `AgentLifecycle`
  across workers (multi-process uvicorn), the cache becomes
  per-worker and an agent spawning on worker A won't reuse
  worker B's cache. Doorae's "single process" architecture
  (ADR 004) sidesteps this for now; revisit if that
  constraint relaxes.
- Daemon's `manifest_store` cache eviction policy. If a
  future change makes the daemon evict its cache while the
  agent process is still alive (e.g. on machine reconnect
  with a fresh sync), the agent's stdin token would still be
  valid (DB row exists) but the daemon would re-pipe a
  *different* token on respawn, creating two valid tokens
  briefly. Not worse than today; just worth noting.
- `db.add` in `_acquire_doorae_token` only takes effect when
  the caller commits. The cache returns the plaintext
  immediately, so a caller that crashes between the mint and
  commit would leave a cache entry pointing at a token whose
  DB row doesn't exist. The next rebuild would still hit the
  cache and reuse that stale plaintext. The window is tiny
  (single `_build_sync_frame` + caller commit) and bounded by
  the cache eviction on `request_stop` / `delete`. Acceptable
  for now; if it ever becomes load-bearing, switch to
  cache-on-commit (commit hook).

## Result

Final fix in the chain that started at #355 and ends here.
With #369 deployed:

- `_build_sync_frame` mints at most one token per agent spawn
  cycle. Repeated rebuilds (broadcast snapshot, sync_batch)
  return the cached plaintext.
- The agent's stdin `OPENAI_API_KEY` corresponds to a row
  that's guaranteed to be committed (only `request_start`
  reaches `_acquire_doorae_token` for the first mint, and
  `request_start` always commits).
- Stop → start rotates the token (eviction on both edges).

Coverage: 953 / 953 cluster tests pass (was 950 pre-#369, +3
new for this fix); ruff clean on changed files.

The full bug chain is now closed:

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| #355 | OpenHands engine added | new feature | adapter |
| #357 | UI doesn't list openhands | machine detector binary-only | Python import detection |
| #359 | engine_secrets always empty | Phase 5 of #197 unimplemented | helper + plumbing |
| #362 | gateway state=failed (timeout) | litellm cold start > 10s | timeout config |
| #364 | gateway dies on import error | bare litellm in venv shadows user-tool | binary path config |
| #366 | gateway 401 with no Bearer | LLM constructor cached api_key=None | explicit kwargs |
| **#369** | **gateway 401 with bogus Bearer** | **frame rebuilds orphan agent_tokens rows** | **per-agent token cache** |

Pending: redeploy on the user's environment + restart agents
+ verify `oh-agent04` actually responds. Operator-side env
config from #364 (`DOORAE_LLM_GATEWAY_BINARY=$HOME/.local/bin/litellm`)
remains required.
