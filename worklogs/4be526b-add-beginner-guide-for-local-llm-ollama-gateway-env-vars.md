# docs(readme): add beginner guide for local LLM (Ollama) + gateway env vars

- Commit: `4be526b` (4be526b3c0085359cb7016860a80c1abe2a7bb2e)
- Author: Changyong Um
- Date: 2026-06-04T22:00:24+09:00
- PR: —

## Situation

The root README was a 70-line, developer-only document: it assumed a git checkout
(`make setup` / `make dev`) and said nothing about installing from PyPI, connecting a
local LLM, or the LLM gateway. A real onboarding session surfaced exactly the gaps a
beginner hits — they could not tell why only `codex` appeared as an engine, how to point
agents at a local Ollama, that LiteLLM must be installed separately, that
`ANYGARDEN_CLUSTER_EXTERNAL_URL` must not be `0.0.0.0`, and that a registered model name
has to actually exist in Ollama. None of this was documented.

## Task

- Turn the README into a beginner onboarding doc without bloating it or duplicating the
  per-package docs.
- Capture the local-LLM (Ollama + OpenHands gateway) path end-to-end.
- Document the gateway environment variables that actually mattered during troubleshooting.
- Keep every command and link accurate to the real CLI and repo layout.

## Action

`README.md` (root) rewritten/extended:
- **Quick Start** split into "Try it" (`uvx --from "anygarden[server]" anygarden server init`
  / `server`, `anygarden machine run`) and "Develop" (`make setup`/`make dev`), plus a note
  that agent engines are auto-detected per machine (explains the codex-only dropdown).
- New **"Run agents on a local LLM (Ollama)"** section: 5 steps (install `litellm[proxy]`,
  enable gateway, register model via "Load models", install `openhands-sdk` extra on the
  machine, create the OpenHands agent).
- New **gateway environment-variable table**: `ANYGARDEN_LLM_GATEWAY_ENABLED`,
  `ANYGARDEN_CLUSTER_EXTERNAL_URL`, `ANYGARDEN_LLM_GATEWAY_BINARY`, `ANYGARDEN_LLM_GATEWAY_PORT`,
  `ANYGARDEN_LLM_GATEWAY_HEALTH_TIMEOUT_SEC`, plus a `LITELLM_LOG=DEBUG` tip.
- **Common gotchas** table mapping each failure (no openhands, empty Usage, FAILED gateway,
  model-not-found, model-failed-to-load) to cause and fix.
- **Documentation** section now links `docs/runbook/`.
- Verified: CLI form `anygarden server init` (init is a subcommand of the server group that
  `dispatch` passes through, not a top-level `anygarden init`); all 6 doc links resolve;
  env var names/defaults checked against `packages/cluster/anygarden/config.py`.

## Decisions

No `.tmp` plan or issue — this was a direct doc request following a live troubleshooting
session, so the rationale comes from that session:
- **What to include**: prioritized the local-LLM path because every blocker in the session
  was there. Deliberately kept cloud-provider/TS-agent/MCP details out to avoid bloat —
  the README points to per-package docs and the runbook instead.
- **Env vars: dedicated table vs prose**: chose a table tied 1:1 to the gotchas rows.
  Prose mentions (the prior README style) had hidden `CLUSTER_EXTERNAL_URL` mid-paragraph,
  which is exactly the var that caused a multi-step debugging detour. A table makes the
  escape hatches (`BINARY`, `PORT`, `HEALTH_TIMEOUT_SEC`) discoverable.
- **English vs Korean**: kept English to match the existing README and the public
  PyPI/GitHub audience, even though the session was in Korean.
- **`anygarden init` corrected to `anygarden server init`**: verified against `cli.py` —
  the unified `dispatch` only routes `server|machine|agent|client`; `init`/`migrate` live
  under the server group. A wrong top-level form would strand a beginner immediately.
- **Assumption**: the unified-CLI subcommand layout (#405/#396) stays stable. If `init`
  is ever promoted to a top-level dispatch command, the Quick Start needs revisiting.

## Result

- README now carries a beginner-friendly install + local-LLM guide with an accurate env-var
  table and a troubleshooting matrix drawn from real failures.
- Pure docs change — no code or behavior affected. Commands and links verified against the
  current CLI (`dispatch`/server group) and `config.py` defaults.
