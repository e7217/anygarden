# feat(mcp): auto-register doorae self-MCP via Streamable HTTP (#277)

- Commit: `6594444` (659444497ced20ee4fc0913fb45b1efe7a8be6aa)
- Author: Changyong Um
- Date: 2026-04-26T01:39:40+09:00
- PR: #277

## Situation

Phase 1 (#266) shipped `mark_task_status`, Phase 3 (#270) shipped
`create_task`, and #120 had already added four skill tools to the
cluster's `/mcp/rpc` endpoint. All six tools sat there waiting to be
called, but #275's e2e proved nobody was calling them: the assignee
LLM happily replied to the synthetic mention message with text but
never invoked the tool, even with the embedded self-instruction.
Inspecting `~/.doorae/agents/<id>/.mcp.json` showed why — only the
admin-attached external MCP servers (e.g. github) were registered;
doorae's own server wasn't in the file at all. The LLM's tools/list
inventory therefore never included `mark_task_status`, so the LLM
treated the instruction as a literal sentence rather than a callable.

## Task

- Make every spawned agent's settings file (`.mcp.json` /
  `.gemini/settings.json` / `.codex/config.toml`) include doorae as
  a default MCP server, before any admin overlays.
- Use the modern MCP transport (Streamable HTTP) since all three
  CLIs now support it — confirmed by direct `mcp add` invocations
  during planning.
- Keep the bearer token off disk on engines that natively allow it
  (codex's `bearer_token_env_var`); accept disk storage on
  claude-code / gemini-cli where the CLIs don't yet provide
  placeholder interpolation.
- Preserve the existing escape hatch: an admin who deliberately
  attaches an external server named `doorae` should still win on
  key collision.

## Action

- `packages/cluster/doorae/config.py` — `DooraeSettings.cluster_external_url`
  (string, default `""`) plus `cluster_external_url_or_default()`
  helper that strips a single trailing slash on explicit values and
  falls back to `http://{reachable_host()}:{port}` when unset. Tests
  in `tests/test_config.py::TestClusterExternalUrl` cover both paths
  and the wildcard-host rewrite.
- `packages/cluster/doorae/mcp_templates/merge.py` — exports
  `DOORAE_BUILTIN_NAME = "doorae"`, `DOORAE_TOKEN_ENV_VAR =
  "DOORAE_AGENT_TOKEN"`, and `doorae_default_entry()`. The helper
  shapes a `RenderedInstance` per engine: claude-code/gemini-cli
  get `{type, url, headers}`; codex gets `{url, bearer_token_env_var}`.
  Engines without MCP support (echo / openai / anthropic / unknown)
  return `None` so callers can drop the result into `overlays`
  without a guard. New file `tests/test_mcp_templates_self_default.py`
  pins the rendered shapes and verifies the codex form keeps the
  plaintext off the manifest.
- `packages/cluster/doorae/scheduler/lifecycle.py` — `AgentLifecycle`
  takes a new `cluster_external_url` kwarg (None = self-MCP off,
  used by tests that don't exercise spawn-time MCP wiring).
  `_build_sync_frame` mints a fresh `AgentToken` per spawn frame
  (plaintext is unrecoverable from DB hashes) and renders the
  doorae default entry into `overlays` *after* admin attachments —
  the order matters because `merge_for_engine` uses
  `dict.setdefault` semantics, so admin entries with the same name
  win. The plaintext rides out on a new top-level
  `doorae_mcp_token` field of the spawn frame for the codex-side
  process-env path. New scenarios under
  `TestDoorAESelfRegistration` in
  `tests/test_mcp_templates_lifecycle.py` cover claude-code, codex,
  cluster-url-unset, admin override, and admin-coexistence cases.
- `packages/cluster/doorae/app.py` — wires
  `cluster_external_url_or_default()` into the lifecycle factory.
- `packages/machine/doorae_machine/protocol/frames.py` —
  `SyncDesiredStateFrame.doorae_mcp_token: str | None = None`.
- `packages/machine/doorae_machine/manifest_store.py` —
  `_EXCLUDED_FIELDS` now drops `doorae_mcp_token` from disk; new
  `_doorae_token_cache` mirrors the existing `_secrets_cache`
  pattern, with `get_doorae_mcp_token()` exposing it to the daemon.
  Cold start without a follow-up sync frame returns None and codex
  tool calls fail until the cluster reconnects — same contract as
  `engine_secrets`.
- `packages/machine/doorae_machine/daemon.py` — passes the cached
  token into `SpawnManifest`.
- `packages/machine/doorae_machine/spawner.py` — `SpawnManifest`
  carries `doorae_mcp_token`; the spawner exports it as
  `DOORAE_AGENT_TOKEN` in the agent process env when the field is
  set. The disk path that already plants the token via the
  Authorization header for claude-code / gemini-cli still works
  unchanged.

## Decisions

Where the doorae entry sits in the merge — three options on the
table (plan §3.2 결정 1):

- **(A) Prepend as the first overlay** — minimal code (one
  `overlays.append(...)` after admin attachments) but reading
  `merge_for_engine`'s `setdefault` semantics revealed it would
  *prevent* admin override.
- **(B) Append after admin overlays** (chosen) — admin attachments
  with the same name reach `mcpServers.<name>` first via
  `setdefault`, so they win. The doorae entry slips into the gap
  on the common case.
- **(C) Separate merge stage** — explicit "defaults → admin"
  pipeline. Cleaner conceptually but doubles the merge code path
  and the existing helper already encodes the precedence we want.

Picked (B). The decisive observation was that the merge helper
*already* spells out "admin wins on key collision" (#124's
docstring), so the right place to plug the default in is the same
overlay list that admin attachments ride. New test
`test_admin_attachment_overrides_default` pins this contract so a
future "make defaults always win" refactor will fail loudly.

Token transport per engine — the cross-engine `mcp add` invocations
during planning revealed that codex's `bearer_token_env_var` keeps
the secret off the manifest while claude-code 2.1.120 and gemini-cli
0.39.1 don't yet support placeholder interpolation in their JSON
files. We split:

- claude-code / gemini-cli: literal `Bearer <token>` in the
  `Authorization` header. Already plaintext-equivalent because the
  same JSON files carry admin-attached secrets the same way (#124
  policy).
- codex: `bearer_token_env_var = "DOORAE_AGENT_TOKEN"`, with the
  spawner exporting that env var in the subprocess.

This means codex agents get strictly tighter token storage than
claude-code/gemini today. Closing the gap on the JSON-side engines
is a separate problem (placeholder interpolation in upstream
CLIs) — out of scope here.

Token lifecycle — every spawn frame gets a fresh token. The DB only
holds hashes, so we cannot reuse a previous plaintext, and rotating
on every frame matches the existing AgentToken pattern
(`handle_token_request` already issues a new row each call).
Manifest writes drop the token from disk for the same reason
`engine_secrets` are dropped: a daemon cold start should rely on a
fresh sync frame from the cluster, not a long-lived plaintext on
disk. The window between cold start and that frame is documented
in `manifest_store.py` — codex tool calls fail with 401 until the
cluster reconnects, which we judged acceptable since the cluster
pushes frames eagerly on every state transition.

cluster_external_url default — `reachable_host()` already encodes
"a host clients can dial," so reusing it as the fallback meant the
single-host dev case needs zero new config. Tests pin both the
fallback and the explicit-override paths.

Assumptions worth flagging:
- Three target CLIs continue to support Streamable HTTP. Major
  upgrades (codex 0.2 / claude-code 3.x / gemini-cli 1.x) need a
  smoke test against this code path before rolling out broadly.
- Admin documentation needs to call out `doorae` as a reserved
  template name; future work in admin UI could surface a warning
  on attach.
- `cluster_external_url` empty + production deploy = agents try
  to reach localhost from a remote machine. The fallback log
  doesn't currently warn; if this bites us, add a startup log
  when the value is empty *and* the machine isn't 127.0.0.1.

## Result

- Cluster pytest 795/795 green (was 778 — added 17 new across
  config, merge, lifecycle).
- Machine pytest 313/313 green (no regressions; no new tests for
  the spawner env injection — the existing spawn unit tests
  already cover env wiring shape).
- The doorae self-MCP now appears as the first entry in every
  spawned agent's settings file (verified by the integration tests
  in `test_mcp_templates_lifecycle.py`).
- Codex's `.codex/config.toml` does not contain the plaintext
  token (verified by negative assertion in
  `test_codex_uses_env_var_indirection`).
- Out of scope, captured in plan §6 and §7:
  - Disk token plaintext on claude-code / gemini-cli (tracked but
    not closed)
  - Cold-start token gap on codex (acceptable per the manifest_store
    docstring)
  - Manual e2e of the original #275 scenario — tools/list will now
    surface `mark_task_status`; a follow-up pass with the dev
    server can confirm the LLM picks up on the embedded
    self-instruction once the tool is actually callable.
