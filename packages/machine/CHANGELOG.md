# CHANGELOG


## v0.3.0 (2026-04-16)

No code changes this cycle — version bumped to keep the three
monorepo packages aligned, per the "they all go together"
release cadence established in v0.2.0.


## v0.2.0 (2026-04-15)

### Features

- Log the resolved doorae-agent binary on every spawn
  ([#38](https://github.com/e7217/doorae/pull/38))
  — new ``agent_binary_resolved`` structlog event with
  ``source=(path|uvx)`` and the absolute path (or ``None`` for
  the uvx fallback). Forensic breadcrumb for "which
  doorae-agent actually ran?" version-skew debugging. No
  change to the discovery priority.

### Earlier (post-0.1.0, no separate release)

- Hide ``max_agents`` from user-facing surfaces
  ([#3](https://github.com/e7217/doorae/pull/3))
- Per-agent model + reasoning effort selection
  ([#5](https://github.com/e7217/doorae/pull/5))


## v0.1.0 (2026-04-14)

Initial release — daemon that hosts agent subprocesses, publishes
heartbeats over WebSocket, and reconciles the cluster's declarative
desired-state for spawn / stop / drain operations.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
