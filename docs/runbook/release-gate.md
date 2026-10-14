# Release gate

A release tag (`anygarden-v<version>`) builds and publishes only after the
`release-gate` job in `.github/workflows/release.yml` passes on the tag's commit.
That job reruns frontend Vitest, the deterministic Chromium Playwright suite and
the production build. These tests use only local fixtures and never receive a
database, an engine credential or an external provider connection.

## Real-engine check before tagging

CI does not call a real engine. Before pushing the tag, start an integrated node
from the release commit ([`local-node.md`](local-node.md)). Then mention one
`codex-cli` agent and one `pi-cli` agent in a room and confirm that each one
replies. This catches engine CLI, authentication and output-format breakage that
the fake-subprocess tests cannot see.

The `Engine Smoke` workflow (#566) used to gate releases on a single codex call
from an isolated self-hosted runner. It never passed and blocked every release,
so it was removed (#835).
