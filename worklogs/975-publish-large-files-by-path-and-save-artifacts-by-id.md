# feat(artifacts): publish large files by path and save artifacts by id (#975)

- Commit: `36d4d82` (36d4d8206a3a0e2a9da1421bcf75d353acca47a5)
- Author: Changyong Um
- Date: 2026-10-30T08:54:10+09:00
- PR: — (issue #975)

## Situation

`publish_project_artifact` only accepted an inline `content` string, so publishing a large report required reading the whole file into the tool argument. This saturated the tool argument size, bloated the engine's event line (tracked as #974), and made it impossible for an agent to publish any artifact larger than the MCP content cap without splitting it artificially. No way existed to save an artifact back to the agent's working directory; agents wanting to process a large published artifact had to paginate through `read_project_artifact` in many calls.

## Task

- Add a `path` parameter to `publish_project_artifact` so the server can copy a file from the agent's machine before the tool transaction, avoiding large inline arguments.
- Add a new `save_project_artifact` tool that writes a readable artifact as a new file in the agent's working directory.
- Share the machine-to-server and server-to-machine file transfer logic between the admin file manager and these two new tool flows.
- Map all transfer failure modes to typed tool error codes (Requirement EXE-24).
- Cap transferred bytes at the 10 MiB artifact limit and apply the same scope + SHA-256 checks already used by `read_project_artifact`.

## Action

- **`packages/cluster/anygarden/workspaces/agent_files.py`** (new, 184 lines): Central module for moving one work file between an agent's machine and the server. Exports `fetch_agent_file` (machine → server) and `send_agent_file` (server → machine). Both obtain a one-time ticket via `transfers.issue` in a short session, then ask the machine over its WebSocket to move the bytes over HTTP through the spool directory, and discard the spool entry in a `finally` block. `placement()` validates the path, resolves the agent's machine, and asserts the `workspace_transfer` capability. `transfer_timeout()` scales the deadline by payload size (30 s base + 10 s/MiB, capped at 600 s).
- **`packages/cluster/anygarden/mcp/project_tools.py`** (lines 182–700): Added `path` to `publish_project_artifact` and made `filename`/`content` optional (exactly one of `content`/`path`); added the `save_project_artifact` tool schema. Introduced `_with_published_file()`: proves the turn, fetches the file from the machine before the tool transaction opens, and returns the augmented `arguments` dict with `data` in place of `path`. Added `_agent_file_conflict()` mapping `AgentFileError.status` → six `ARTIFACT_*` error codes with human-readable hints. Wired `save_project_artifact` into `call_project_tool`: loads the artifact via `load_authorized_artifact`, releases the read transaction, then calls `send_agent_file`.
- **`packages/cluster/anygarden/project_executions/artifacts.py`**: Exported `MAX_ARTIFACT_BYTES` (the 10 MiB cap) and adjusted `publish_project_artifact` to accept `data: bytes` in addition to `content: str`.
- **`packages/cluster/anygarden/project_executions/artifact_reader.py`**: Extracted `load_authorized_artifact` as a shared helper used by both `read_project_artifact` and `save_project_artifact`.
- **`packages/cluster/anygarden/project_executions/approvals.py`**: Extended the approved-action byte limit to follow `MAX_ARTIFACT_BYTES`.
- **`packages/cluster/anygarden/workspaces/managed_router.py`**: Router now delegates file-manager upload/download routes to `agent_files` helpers and maps `AgentFileError` statuses to HTTP codes, removing ~40 lines of duplicated transfer logic.
- **`packages/cluster/tests/test_project_execution_flow.py`** (+115 lines): End-to-end tests for `publish_project_artifact` with `path` (happy path through read, both/neither source, missing file, offline machine, too large, non-UTF-8) and `save_project_artifact` (happy path, unauthorized artifact, existing target). Transfers are replaced with a fake.
- **`packages/cluster/tests/test_managed_workspace_api.py`** (+58 lines): A real-daemon test that `fetch_agent_file` and `send_agent_file` move a 2 MiB file and refuse oversize, missing, private, existing and folderless targets.
- **`packages/cluster/tests/test_mcp_server_create_skill.py`**: Adds `save_project_artifact` to the expected tool list.
- **`packages/cluster/anygarden/agent/runtime/execution/pi_self_tools.json`**: Added `save_project_artifact` to the self-tool allowlist.
- **`docs/requirements.md`**: Added requirement EXE-24.

## Decisions

- **Server pulls the file over the #914 transfer (chosen)** vs. the three options listed in #975:
  - *Open the Codex sandbox network and add an `anygarden artifact publish` CLI*: rejected because `standard` Codex runs `workspace-write`, so every shell command would gain network access, the server URL would have to be put in the engine environment, and Pi and remote machines would each need their own path.
  - *Local stdio MCP proxy that intercepts path arguments*: rejected because it needs large per-engine MCP config changes and a proxy process lifecycle.
  - *Server-side pull*: the admin large-file download (#914) already proved "server receives a work file from the agent's machine" with a real daemon, including path safety, private-name refusal, size limits and remote machines. Only the turn-proof boundary and wiring were new.
- **Fetch before the tool transaction**: `transfers.issue` commits its own session, and a transfer can take seconds; holding the tool transaction across it would keep a SQLite write lock. `publish_artifact` re-validates everything afterwards.
- **One `load_authorized_artifact` for read and save** so the save tool cannot drift from read's scope and SHA-256 rules.
- **Text only, parent folder must exist, never overwrite**: these match `read_project_artifact` and the daemon's `receive_into`, and avoid adding mkdir/overwrite semantics now.
- **Revisit if**: an agent is not placed on a machine (federated peers publish to their own server), the engine's working directory stops matching the workspace receipt (`workspace_receipt.py`), or artifacts need to exceed 10 MiB (readers load the whole file into memory).

## Result

- Agents can now publish large text files (up to 10 MiB) by supplying a `path` instead of inline `content`, avoiding argument-size limits and engine event bloat (#974 symptom addressed).
- `save_project_artifact` allows agents to receive a published artifact as a new file in their working directory without reading it in chunks, enabling local tool processing of large documents.
- Transfer failures surface as six distinct `ARTIFACT_*` error codes with actionable hints.
- All changes are covered by new integration tests in `test_project_execution_flow.py` and `test_managed_workspace_api.py`.
- Requirement EXE-24 added to `docs/requirements.md`. Not yet verified on a live node.
