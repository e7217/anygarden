# feat(ui): mark agents placed on a remote machine with a machine badge (#851)

- Commits: work-in-progress commits on `feat/851-remote-machine-badge` (squashed in the PR)
- Author: Changyong Um (with Claude)
- Date: 2026-10-15
- Issue: #851

## Situation

Since #848 a node can also be a machine of another server, so one server's
agents now run on several hosts. The sidebar showed every agent the same way.
On a test pair (develop-vm node with the anygarden-svc node attached as a
machine), the remote `anygarden-svc01` and the local `호스트 에이전트` were
indistinguishable unless the host was written into the name.

## Task

- Show where an agent runs, without changing a single-machine install.
- Decide "local" the way the server already does, not with a new rule.
- Keep machine names and hosts inside the admin audience.

## Action

- **API:** `AgentOut.placement` (`AgentPlacementOut`: `machine_id`,
  `machine_name`, `machine_hostname`, `is_local`). `is_local` compares the
  placed machine with `app.state.local_machine_id`, the same check
  `machines.py`, `workspaces/router.py` and `ws/machine_handler.py` use. A
  server without a built-in machine reports every placement as remote.
  Unplaced agents get `null`.
- `_agent_to_out` stays a pure converter and takes the machine. New
  `_agents_out` loads the machines of a list in one query; `_agent_out` wraps
  it for single responses. All `create`/`update`/`engine`/`get`/`list`/`start`
  responses use them.
- **UI:** `MachineBadge` renders nothing for local or unplaced agents. For a
  remote one it shows a server icon and the machine name, truncated, with the
  host and online state in the tooltip; an offline machine is dimmed. It sits
  after the name in the sidebar agent row and in the agent settings header.
- Requirements NODE-14 (pytest) and NODE-15 (vitest).

Left out on purpose: the room participant list and @mention popover. They are
built from room participants and shown to regular users and guests, while
`AgentOut` is admin-only; exposing machine names there needs its own decision.

## Result

- `tests/test_agents_api.py`: four placement tests (remote, local, server-only,
  unplaced) — failed before the change, pass after.
- `MachineBadge.test.tsx` (4) and a sidebar test that only the remote agent's
  row carries the badge.
- Full suites and the live check are recorded in the PR.
