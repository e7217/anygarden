# chore(release): bump anygarden-agent to 0.13.0

- Commit: `8933cb3`
- Author: Changyong Um
- Date: 2026-09-23
- PR: —

## Situation

A live local node was serving HTTP fine — `/rooms/<id>` returned the SPA in 1.6ms, `/healthz` was green, login issued tokens — but agents answered nothing. Two `안녕` messages in a DM room sat with no reply.

The evidence chain: `agent_turn_outbox` held both turns at `state='pending'`, `delivery_count=0`, `last_error=NULL` — queued but never even attempted. `ss` showed **zero** established connections to port 8001; the only WebSocket handshakes in the node log came from the operator's browser, none from any agent. `~/.anygarden/agents/<id>/agent.log` named the cause outright:

```
ws.disconnected error="http://127.0.0.1:8001/ws/rooms/<id> isn't a valid URI:
                       scheme isn't ws or wss" retry_in=60.0
```

Every room, every agent, backing off forever.

The repo already had the fix. `packages/agent/anygarden_agent/client.py` maps an `http(s)://` base to `ws(s)://` before handing the URL to `websockets` — landed in #637, comment and all. But the *running* agents were executing `client.py:873` from a wheel in the uv cache with no such mapping.

Why: `spawner.py:1148` resolves the agent binary with `shutil.which("anygarden-agent")` and, when PATH misses it, silently falls back to `uvx anygarden-agent` — i.e. the PyPI release. The node had been started without `.venv/bin` on PATH, so it ran published 0.12.0 instead of the workspace editable install.

And the trap that made this invisible: **the repo and PyPI both read `0.12.0`**. #637 was merged without a version bump and never released, so no version comparison could reveal the gap. The last release was 2026-07-17; eleven agent-package commits had accumulated behind it.

## Task

- Publish the accumulated agent work so the `uvx` fallback path stops serving a client that cannot connect.
- Pick a version that honestly describes the delta rather than reusing 0.12.0.
- Record the change so the next person reading the CHANGELOG sees why this release exists.

## Action

- `packages/agent/pyproject.toml` — `version = "0.12.0"` → `"0.13.0"`; `uv.lock` regenerated to match.
- `packages/agent/CHANGELOG.md` — the existing `## Unreleased` content became `## v0.13.0 (2026-09-22)` with a header noting this is the first release since v0.12.0 and why it matters. Added `### Added` (#562, #600, #619, #621, #633, #640, #642) and expanded `### Fixed` with the #637 scheme mapping described by its observable symptom — silent agents, turns stuck in `agent_turn_outbox` — not just the code change. The pre-existing `### ⚠ Breaking changes` entry for #644 was left as written.
- Rebased onto `main` after #679 (Codex/Pi engine consolidation) merged, and folded it into the same entry: two new breaking items (the `claude-code` / `gemini-cli` / `openhands` engines and their SDK dependencies and extras are gone), `### Added` lines for shared room execution and direct endpoint flags (`--provider`, `--endpoint-configured`), and `### Fixed` lines for the cancellation/WebSocket and stdin-credential fixes. Release date moved to 2026-09-23.

Verification before building:
- `uv run pytest` in `packages/agent` — **587 passed** before the rebase; **522 passed** after it (the drop is the removed engines' test files, not skipped tests).
- `uv run ruff check packages/` reports 1800 errors repo-wide, unchanged by this commit (the diff is one version string plus a CHANGELOG); lint is not a gate here because it already fails on `main`.
- `uv build --package anygarden-agent` produced `anygarden_agent-0.13.0-py3-none-any.whl` and `.tar.gz`.
- Unzipped the wheel and asserted the artifact actually carries the fix: `ws_base.startswith(("http://", "https://"))` present, `Version: 0.13.0` in METADATA. Repeated after the rebase: the wheel also ships only the `codex_cli` / `pi_cli` integrations, and its `Requires-Dist` no longer lists `claude-agent-sdk`, `openhands-*` or `fastapi`. This is the check whose absence caused the whole incident, so it was made explicit rather than assumed.
- `twine check dist/*` initially failed with `'2.5' is not a valid metadata version` — twine 6.2.0 predates the Metadata-Version uv now emits. Upgraded to twine 7.0.0; both artifacts then PASSED.

## Decisions

- **Minor (0.13.0), not patch (0.12.1).** The release carries #644, which removes `ChatClient.is_collaborative()` and the `with_collaborative_hint` parameters — a breaking API change — plus six feature commits. Under 0.x, breaking changes go in the minor slot. Calling it a patch would tell release-note scanners "safe bugfix" about a release that deletes public methods.
- **Release the whole accumulated range rather than cherry-picking #637.** A 0.12.1 containing only the scheme fix would need a branch off the v0.12.0 tag and would leave the other ten commits still unreleased — the same trap, reset for next time. The eleven commits are already on `main` and tested together.
- **Verify the built wheel's contents, not just the version number.** The root cause here was precisely that a version number matched while the code did not. Checking the string inside the artifact is the only step that would have caught it.
- **Left ruff failures alone.** 1800 pre-existing errors across the repo; fixing them inside a release commit would bury the one-line version change under unrelated churn.
- **Include #679 in 0.13.0 rather than holding it for 0.14.0.** It merged to `main` before this release shipped, so a 0.13.0 tag cut from `main` contains it anyway; leaving it out of the CHANGELOG would publish an engine removal with no note. It is already a minor bump for #644, so no version change was needed.
- **Did not bump `anygarden` (cluster) or `anygarden-machine`.** The local node runs cluster from the workspace editable install, so it was never affected. Widening the release would mean validating two more distributions for a problem neither has.

## Result

- `anygarden-agent 0.13.0` built and validated locally; artifacts pass `twine check`.
- The immediate outage was resolved independently by restarting the node with `PATH="$PWD/.venv/bin:$PATH"`, which makes `shutil.which` resolve the workspace install instead of falling back to `uvx`: agents reconnected (`ws.connected` + `ws.welcome` on all five rooms), established connections to 8001 went 0 → 50, and **both queued turns flipped from `pending` to `delivered`** — the agent answered the messages that had been stranded. No data was lost; the outbox replayed on its own.
- Remaining risk, unaddressed here: the fallback is silent by design. A node started without `.venv/bin` on PATH will still quietly run the PyPI build, and the only symptom is agents that never speak. Worth considering whether `agent_binary_resolved` with `source="uvx"` should warn, or whether the spawner should pass a `ws://` base so the agent never has to compensate.

## Pending

- `twine upload dist/*` to PyPI (credentials present in `~/.pypirc`).
- Push `chore/release-agent-0.13.0` and open a PR.
- Tag `anygarden-agent-v0.13.0` to trigger the release workflow.
