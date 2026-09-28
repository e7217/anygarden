# feat(workspace): edit managed workspace files from agent settings

- Commit: `522bf4f` (522bf4f73f131ce693c816d16470025b3ba637df)
- Author: Changyong Um
- Date: 2026-09-28T13:10:48+09:00
- PR: —

## Situation

Agent settings could browse an agent's managed workspace on its machine (admin only, through the authenticated MachineBus request path), but could not change it. The only editable file surface was `/agents/{id}/files`, a manifest delivered on the next run, which does not touch the files an agent is actually working with on disk, and cannot reach remote machines from the cluster host.

## Task

- Let admins create folders, upload new files (≤1 MiB) and edit small UTF-8 text files (≤64 KiB) in the managed workspace from the web UI.
- Keep the existing read boundary: admin only, current placement/generation, no external folders or other agents' workspaces, no hidden secret files, links, special files or hardlinks.
- Never overwrite on create; detect concurrent changes to text files; never persist or log file bodies.
- Stay compatible with older read-only daemons.

## Action

- machine: `packages/machine/anygarden_machine/managed_workspace.py` adds `EDIT_CAPABILITY = "managed_workspace_edit_v1"`, size limits, `_edit_token`, `_existing_kind`, `_write_atomic` (temp file in the same directory, fsync, `os.link` for create-only / `os.replace` for edits) and `mutate()` for mkdir/upload/write, walking parents with `O_NOFOLLOW` directory descriptors and rejecting non-regular or multiply-linked entries. `daemon.py` advertises the capability and routes the new operations; `protocol/frames.py` extends `ManagedWorkspaceRequestFrame` with `operation` values `mkdir|upload|write` and `edit_token`, `content_base64`, `text`, `expected_sha256` fields.
- cluster: `packages/cluster/anygarden/workspaces/managed_router.py` adds `POST /{agent_id}/workspace/folder`, `POST /{agent_id}/workspace/upload` and `PUT /{agent_id}/workspace/file`; `_query` sets `can_edit` from the machine's capabilities, returns `unsupported` for edits on read-only machines, re-checks placement/generation after the machine round trip (409 on change) and maps machine conflicts to 409. `scheduler/machine_bus.py` forwards the new request fields.
- frontend: new `ManagedWorkspaceEditor.tsx` (editor, new folder/file, upload with client-side size checks); `ManagedWorkspacePanel.tsx` mounts it only when `can_edit`; `useManagedWorkspace.ts` adds `can_edit`, `sha256`, `edit_token` types; `agentSetup.ts` adds ko/en copy.
- design: `docs/plans/2026-09-27-managed-workspace-editor-design.md` records the contract and condition table.
- tests: machine `tests/test_managed_workspace.py` (+153) covers mutation boundaries; cluster `tests/test_managed_workspace_api.py` extends the real-disk round trip (remote and integrated delivery) with create/edit/upload, stale-hash and duplicate-upload 409s, non-admin 403, and adds `test_read_only_machine_rejects_edits`; `ManagedWorkspaceEditor.test.tsx` covers the editor.

## Decisions

- Options weighed (from the design doc):
  - Edit the DB manifest (`/agents/{id}/files`) — rejected: it only applies on the next run and does not change the current disk.
  - Open paths on the cluster host — rejected: cannot reach workspaces on remote machines.
  - Extend the authenticated MachineBus workspace request and the machine's own disk access — chosen: reuses the admin/placement boundary already enforced for browsing and works for remote and integrated nodes.
- Optimistic concurrency with the SHA-256 returned on read (`absent` for new files) instead of locks: agent processes do not take web-edit locks, so the hash check detects, but does not fully serialize, races with an agent editing the same file.
- Create operations never overwrite; conflicts surface as 409 so the UI can keep the user's input.
- A separate `managed_workspace_edit_v1` capability keeps older read-only daemons browsable while hiding edit tools.
- Revisit if: agents start holding file locks, size limits prove too small, Windows machines need support (the POSIX descriptor walk is not available there), or server and machine packages can be deployed independently (the design assumes they are updated together).

## Result

Admins can create folders, upload new files and edit small text files in an agent's managed workspace from agent settings; read-only machines keep browsing with edit tools hidden and edit requests answered `unsupported`. Verified before commit: cluster full suite 1961 passed, managed workspace API 6 passed, machine 572 passed, frontend 713 passed, frontend build OK. Pending: rebase onto current `main` (which gained #718 in agent settings) and PR.
