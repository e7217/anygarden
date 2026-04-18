# feat(cluster): skills.sh search proxy + stale check for skill library (#126)

- Commit: `78454db` (78454db9ae3957d7d03e2b77ab26e537c062b5ea)
- Author: Changyong Um
- Date: 2026-04-19T02:35:12+09:00
- PR: #126

## Situation

After Phases 1–3 of the skill library landed on `main`, admins could register, approve, and attach shared GitHub-backed skills, but they still had to know the exact `owner/repo` + skill name ahead of time and had no signal when upstream moved past the pinned commit. The anonymous GitHub rate limit (60/h/IP) also became a live concern once Phase 3's per-skill raw fan-out multiplied the fetch count. Discovery, drift detection, and authenticated fetching were the three remaining gaps before the library could be considered operational.

## Task

- Add a skills.sh search proxy so the admin UI can discover and one-click-register skills without leaving the dashboard.
- Detect upstream drift on registered skills so admins see an "Update available" signal; refresh individual skills against HEAD on demand.
- Let the fetcher pick up `GITHUB_TOKEN` from env for both `api.github.com` and `raw.githubusercontent.com` requests.
- Preserve Phase 2's approval gate across refresh: a new pinned_rev must re-enter review, not auto-promote.
- No DB schema changes (stale state lives in memory).

## Action

- `packages/cluster/doorae/skills_library/github_fetcher.py:118-202` — added `token` constructor arg (falls back to `os.environ["GITHUB_TOKEN"]`), `_auth_headers()` helper applied to tree and raw GETs, and `resolve_head_sha(source, rev)` that issues only the tree API call so the cron probe cost is one request per skill.
- `packages/cluster/doorae/skills_library/search.py` (new, ~120 lines) — `search_skills(query, limit)` + `SearchResult` dataclass wrapping skills.sh `/api/search`. Defensive per-row parsing so a single malformed entry doesn't blank the list; raises `SkillSearchError` on non-2xx / bad JSON.
- `packages/cluster/doorae/skills_library/service.py:134-170,760-870` — added `StaleCheckResult` dataclass, `check_stale(db, id)` for single-probe, `check_all_stale()` for cron sweeps. `agent-authored:` rows short-circuit to `stale=False`. Rate-limit mid-sweep breaks the loop (defers to next tick); other fetch errors record `error=...` but continue. The existing `_SkillFetcher` Protocol now also requires `resolve_head_sha`.
- `packages/cluster/doorae/api/v1/skills.py:44-58,182-218,591-719` — new `SkillSearchResultOut` / `SkillStaleOut` schemas; `SkillOut.stale: bool` merged from `app.state.skill_stale_cache`. Added `GET /admin/skills/search` (60s TTL dict cache, 100-entry cap, 502 on upstream error), `GET /admin/skills/stale`, `POST /admin/skills/:id/refresh` (re-registers, clears stale marker, 400s on agent-authored rows).
- `packages/cluster/doorae/app.py:257-266,371-478` — lifespan now seeds `skill_stale_cache`/`skill_search_cache` dicts, spawns `_run_skill_stale_cron(app, interval_seconds)` as an `asyncio.Task` unless disabled (`DOORAE_SKILL_STALE_INTERVAL_HOURS=0` or a sentinel on `app.state.skill_stale_task`). Shutdown cancels and awaits the task. Cron replaces the cache wholesale each sweep so deleted skills don't linger.
- `packages/cluster/frontend/src/components/AdminSkills.tsx` — added search dialog with per-row Register buttons + spinner, stale `<Badge>` inline with existing status badges, per-card Refresh button hidden on agent-authored rows, refresh/register spinner state via `Set<string>` trackers.
- `packages/cluster/tests/test_skills_library_stale_and_search.py` (new, 19 tests) — covers `GITHUB_TOKEN` header injection, `resolve_head_sha` issuing zero raw fetches, skills.sh parsing (including malformed rows + 5xx), stale detection and rate-limit halt semantics, API search caching (60s memo), stale endpoint, refresh creating a new pending row on drift, refresh idempotency on same SHA, and cron drive-through with cancellation.

## Decisions

Plan `.tmp/plan-126-skill-search-proxy-and-stale-check.md` flagged three re-review points needing Phase 2/3 outcomes before locking; each was resolved in the implementation:

- **Stale cache location (plan Decision A)**: kept in-memory on `app.state` (A1). A DB column (A2) would have required migration 022 and the cache becomes stale again within 6h anyway; Redis (A3) is overkill for single-server doorae. Violating assumption: when multi-server deploys happen, this flips to needing shared cache.
- **Post-refresh re-approval (plan Decision B)**: went with B1 (approval required). `service.register` already creates a sibling row on new pinned_rev with `approved_by=NULL`, which is the safe default against supply-chain attacks where upstream is compromised. B2 (keep existing approval) was tempting for ops comfort but trades security for convenience — rejected. Verified by `test_refresh_endpoint_creates_new_row_when_upstream_moved`.
- **content_hash usage for stale detection**: stale check stays SHA-only (cheap probe). The canonical tree hash from Phase 3 then weighs in at refresh time via `service.register`'s existing `body_changed` comparison — a "new SHA same content" case doesn't gratuitously bump attached agents. Matches plan §3.1.
- **GITHUB_TOKEN injection (plan Decision D)**: env var (D1). `DooraeSettings` field (D2) would have needed deployment-doc churn; DB storage (D3) was rejected outright as it would put a plaintext credential in a DB backup.
- **Search cache TTL (plan Decision C)**: 60s dict keyed on `(query, limit)` with a 100-entry eviction cap. Simpler than a scheduled async index mirror and enough to absorb admin typing latency.
- **Cron scheduler**: naive `asyncio.create_task` + `while True: await asyncio.sleep(interval)` rather than APScheduler. Plan §2.3 accepted this for now; upgrade path noted if frequency or jitter ever matters.

## Result

571 cluster tests pass, 19 of them new; `uv run ruff check` is clean on every changed file (the 2 pre-existing `F811` warnings on `app.py`'s duplicate `text` import are unchanged). `npm run build` succeeds in 9s with the usual chunk-size warning. Agent + machine test suites unchanged by these changes (one pre-existing `test_integrations/test_openai.py` failure depends on `OPENAI_API_KEY` and exists on `main`).

Behavior changes shipped to admins: skills.sh search dialog with one-click registration; "Update available" badge on cards when upstream drifts; per-card Refresh button; list response now carries `stale: bool`; `GET /admin/skills/search|stale` and `POST /admin/skills/:id/refresh` endpoints; `GITHUB_TOKEN` env var supported for both REST and raw fetches; 6h stale-sweep cron with `DOORAE_SKILL_STALE_INTERVAL_HOURS` override (`0` disables).

Not shipped this phase: webhook-based real-time drift detection (Phase 6), APScheduler migration, admin UI toggle for B1 ↔ B2 approval policy.
