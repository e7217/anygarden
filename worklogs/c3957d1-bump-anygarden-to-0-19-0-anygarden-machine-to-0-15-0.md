# chore(release): bump anygarden to 0.19.0, anygarden-machine to 0.15.0

- Commit: `c3957d1` (c3957d1e8e87b9d3987afe05dc74118f95880223)
- Author: Claude (assisted, user-directed)
- Date: 2026-08-09
- PR: — (release)

## Situation

PyPI `anygarden 0.18.0` was cut on 2026-07-24 (#555) at alembic revision
**056**. `main` is now at **063**. Seven migrations and roughly two weeks
of merged work have accumulated behind an unchanged version string:

- 057–058 room authorization + audits (#561)
- 059 message threads (#562)
- 060 message-linked task claims (#563)
- 061 durable agent turns (#564)
- 062 workspace attachments (#565)
- 063 lifecycle dispatch lease (#581)

Plus the release-gate work (#566–#577), the public error/CLI
normalization (#573), and the thread UI (#580).

The hazard is not that the release is late. It is that
`packages/cluster/pyproject.toml` still reads `0.18.0`, so **the same
version string denotes two different schemas** — installing from PyPI
yields 056, installing from `main` yields 063, and nothing in the
version number distinguishes them. Cutting a release from `main` today
would attempt to publish `0.18.0` a second time.

## Task

- `anygarden` (cluster) 0.18.0 → **0.19.0**
- `anygarden-machine` 0.14.1 → **0.15.0**
- `pyproject.toml` **and `uv.lock`**.

## Action

Bumped `[project] version` in `packages/cluster/pyproject.toml` and
`packages/machine/pyproject.toml`.

## Decisions

**`uv.lock` is included, breaking with #552 and #555.** Those release
commits deliberately excluded the lockfile, and this change first
followed them — CI rejected it. Phase 0 (#559/#560) added
`uv lock --check` to the test job on 2026-08-06, after those releases.
Workspace members carry their versions in the lockfile, so a bump that
touches only `pyproject.toml` now leaves the two out of sync and fails
the gate. The lock diff is exactly the two version lines.

The older convention is obsolete rather than wrong; it predates the
frozen-lockfile gate by six weeks.

**Minor, not patch, for both.** The cluster gained user-visible features
(threads, workspace attachments, room authorization, durable turns) and
a changed public error contract (#573). The machine package changed in
#581 — `daemon.py`, `manifest_store.py`, and `protocol/frames.py` now
carry the stop-tombstone high-watermark and generation fencing, which
is new behaviour rather than a fix to existing behaviour.

**`anygarden-agent` is not bumped.** `packages/agent` saw only a README
and a CLI test touched by #573; no shipped behaviour changed. This
follows #555, which excluded the agent package on the same grounds.

**No release published by this change.** The bump is separated from
`make release-cluster` / `release-machine` deliberately: publishing to
PyPI is irreversible and outward-facing, and the operator should decide
when it happens. This commit only makes such a release possible without
a version collision.

**`anygarden-machine` floors raised to `>=0.15.0`** in the `server`,
`machine`, and `dev` extras (previously `>=0.8`). This is not
housekeeping. #581 added `generation` to the spawn frame and taught the
daemon to hold a stop tombstone as a persistent high-watermark. A
cluster at 0.19.0 paired with an older daemon still dispatches — the
daemon simply ignores the field, the generation fence does nothing, and
the split-brain #581 closed reopens silently. Nothing at runtime refuses
that pairing, so the dependency floor is the only place it can be
refused.

## Rollout order

**Upgrade remote daemons to `anygarden-machine>=0.15.0` before, or at
the same time as, the cluster.** The reverse order leaves a window in
which the server believes it is fencing and the daemon is not.

- A daemon below 0.15.0 does not persist the stop tombstone, so a stop
  followed by a start on another machine can leave the original
  generation running.
- Once a cluster at 0.19.0 has advanced a generation, legacy daemons are
  out of contract for that agent: their reports are ignored by the
  server, but their processes and side effects are not stopped by it.
- `anygarden machine update` on each host, or **Admin → Machines →
  Update** from the web UI, performs the daemon upgrade.

## Follow-up

Deploying `main` to an existing instance requires the alembic upgrade
through 063. The home-lab instance (ag00) currently runs 0.18.0 at
revision 056 and will migrate 056 → 063 on first start of the new
version.
