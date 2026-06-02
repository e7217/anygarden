# feat(llm-gateway): warn on missing litellm binary at init + spawn (#406)

- Commit: `2241757` (224175729b453dc31f4005967a47e1becbfedd96)
- Author: Changyong Um
- Date: 2026-06-02T10:52:40+09:00
- PR: #406

## Situation

The embedded LLM Gateway (#197) spawns `litellm[proxy]` as a subprocess and reverse-proxies
`/api/v1/llm/*` to it. But `litellm` is declared in no `pyproject.toml` — the `server` extra
only carries `pyyaml`, and the cluster venv can't ship `litellm[proxy]` itself because its proxy
extras pin `fastapi==0.124.4` against cluster's `fastapi<0.120` (#364). Installing litellm is done
only by the developer-oriented `Makefile` (`uv tool install 'litellm[proxy]'`). Users who install
via `uvx --from "anygarden[server]" anygarden init` therefore never get litellm, and enabling the
gateway dropped it into a silent `FAILED` state whose only diagnostic was a raw
`spawn failed: FileNotFoundError(...)` repr.

## Task

- Warn users at install time (`anygarden init`) when the litellm binary isn't reachable, without
  making init fail for users who never enable the gateway.
- Turn the gateway's spawn-time `FileNotFoundError` into an actionable message that names the binary
  and tells the operator how to install it.
- Keep the two surfaces' wording and probe logic in sync.
- No new runtime dependency (the supervisor imports the helper at module scope-adjacent paths).

## Action

- `packages/cluster/anygarden/llm_gateway/binary_check.py` (new, 51 lines) — stdlib-only
  (`os`, `shutil`) single source of truth: `INSTALL_HINT` message, `resolve_litellm_binary()`
  (honours `ANYGARDEN_LLM_GATEWAY_BINARY`, falls back to `"litellm"`), and
  `litellm_available()` (PATH/abs-path probe via `shutil.which`).
- `packages/cluster/anygarden/cli.py:58` — (A) `init` now calls `litellm_available()` after writing
  config; on miss it `click.echo(INSTALL_HINT)` and still prints "Initialization complete." with
  exit code 0 (non-fatal).
- `packages/cluster/anygarden/llm_gateway/supervisor.py:296` — (D) `_do_spawn` gains an
  `except FileNotFoundError` branch ahead of the generic `except Exception`, setting
  `last_error` to `litellm binary not found ({binary!r}). {INSTALL_HINT}` and logging
  `llm_gateway.binary_missing`.
- `packages/cluster/tests/test_cli_init_litellm_check.py` (new, 3 tests) — missing→hint+exit 0,
  present→silent, and `ANYGARDEN_LLM_GATEWAY_BINARY` override is the value probed.
- `packages/cluster/tests/test_llm_gateway_supervisor.py` — added
  `test_missing_binary_yields_install_hint` (spawn_fn raises `FileNotFoundError`; asserts FAILED,
  hint in `last_error`, health probe never awaited).

## Decisions

Plan `.tmp/plan-406-litellm-install-ux.md` weighed the options:

- **D handler location — `_do_spawn` vs `_real_spawn`**: chose `_do_spawn`'s `except` branch.
  `_real_spawn` is production-only (tests inject `spawn_fn`), so logic there would be untestable
  by the existing supervisor unit pattern. Putting it in `_do_spawn` lets
  `spawn_fn=AsyncMock(side_effect=FileNotFoundError)` verify it in one line.
- **A probe without loading settings**: read `ANYGARDEN_LLM_GATEWAY_BINARY` straight from the env
  rather than constructing `AnygardenSettings`. Init is a cheap, side-effect-free file generator;
  loading full settings would drag in validation and new failure points for the one fact (binary
  name) A needs.
- **A is non-fatal**: init runs for every user regardless of gateway use, so a missing binary is a
  warning, not an error — exit code stays 0.
- **Rejected (out of scope)**: adding litellm as a dependency/extra (options B/C) — blocked by the
  fastapi pin conflict (#364).
- **Assumptions to revisit**: users are on `uv` (hint says `uv tool install`); a pip-only install
  path would need different wording. If init and the server ever run under different environments,
  A (env-read) and D (`self._binary` from settings) could disagree — mitigated by mentioning the
  override env var in the hint.

## Result

- 1006 cluster tests pass (incl. 3 new A tests + 1 new D test); ruff clean on changed files.
- `anygarden init` now surfaces the install command when litellm is absent; gateway spawn failures
  due to a missing binary show an actionable `last_error` in the Status panel instead of a bare repr.
- Documentation touch-up (runbook one-liner, plan step 7) was left out as optional/out-of-scope.
