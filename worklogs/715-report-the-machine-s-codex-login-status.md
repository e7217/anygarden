# feat(machines): report the machine's Codex login status (#715)

- Commit: `db0ee64` (see `git log` on branch feat/715-codex-login-status)
- Author: Changyong Um
- Date: 2026-09-28
- PR: —

## Situation

Codex agents do not receive an OpenAI key from Anygarden. The daemon lets `codex` read the machine user's `~/.codex/auth.json` (directly, or symlinked into a per-agent Codex home by `spawner.py`), so an agent works only if someone ran `codex login` on that machine. The cluster had no way to know this; a missing login surfaced only as a failed first turn. Issue #715's agent settings/creation redesign needs to show the login state next to the Codex model choice.

## Task

- Detect how the machine's Codex is signed in without reading credential files.
- Report it on register and when an admin re-checks the engine.
- Persist it across reconnects and expose it through the machine engines API.
- Stay compatible with older daemons that do not send the field.

## Action

- `packages/machine/anygarden_machine/detector.py`: `EngineInfo.auth`; `CODEX_LOGIN_MESSAGES`, `parse_codex_login_status()` and `_codex_login_status()` run `codex login status` (5s timeout) for codex-cli and map its stderr message to `chatgpt` / `api_key` / `other` / `none` / `unknown`. Failures keep the engine detected with `unknown`.
- `packages/machine/anygarden_machine/daemon.py`: register capabilities carry `auth` only for engines that report one; the engine check handler sends it in `EngineCheckResultFrame.auth` (`protocol/frames.py`).
- `packages/cluster/anygarden/db/models.py` + `migrations/versions/078_machine_engine_auth_status.py`: `MachineEngineStatus.auth_status`, `auth_checked_at`.
- `packages/cluster/anygarden/ws/machine_handler.py`: `_record_auth_status()` used by register and `_handle_engine_check_result`; reports without `auth` leave the stored value.
- `packages/cluster/anygarden/api/v1/machines.py`: `MachineEngineOut.auth_status`, `auth_checked_at`.
- Tests: detector parsing/subprocess cases, daemon register and check frames, cluster register/check/API cases; head revision constants in `test_migrations.py` and `test_federation_trust.py` moved to 078 (same pattern as #711).

## Decisions

- Storage in `machine_engine_status` rather than `machine_engines`: register deletes and recreates `machine_engines`, while `machine_engine_status` (#553) is already upserted per (machine, engine) and survives reconnects; check results write to the same row.
- `codex login status` instead of parsing `auth.json`: keeps the daemon from opening credential files (the managed workspace boundary blocks `auth.json` for the same reason). Unrecognised output falls back to `unknown`, so a wording change degrades safely. Messages were confirmed against codex-cli 0.157.1 (stderr; "Not logged in" exits 1); other `Logged in using …` variants (Bedrock, access tokens) map to `other`.
- Re-check reuses `POST /machines/{id}/engines/{engine}/check` instead of a new endpoint: same owner/admin policy and offline handling; the extra registry lookup is acceptable latency.
- Missing `auth` never clears a stored value, so a daemon without the field cannot erase a newer report. Revisit if daemon downgrades become common (the stale value would persist; `auth_checked_at` shows its age).

## Result

The machine engines API now reports the Codex login status, refreshed on every daemon register and engine check. Real detection on a dev machine returned `codex-cli | codex-cli 0.157.1 | chatgpt`. Tests: machine 581 passed; cluster 1960 passed plus the 11 migration tests that needed the new head constant (107 passed on rerun of migration/federation/engine test files); migration upgrade → downgrade → upgrade verified on SQLite. UI display (Part B of the #715 plan) is pending.
