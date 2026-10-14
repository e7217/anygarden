# test(agent): point the Pi self-tools bridge test at the moved harness (#832)

- Commit: `ea7818f` (ea7818fde39abf3bcb9fa7e098e87fa8b5abfd2f)
- Author: Changyong Um
- Date: 2026-10-14T16:13:10+09:00
- PR: #832

## Situation

As part of PR #832 (ship the agent runtime inside the `anygarden` package), the test fixture `pi_self_tools_harness.mjs` was relocated from `packages/agent/tests/fixtures/` into `packages/cluster/tests/agent/fixtures/`. The integration test `test_pi_self_tools_bridge.py` still held the old absolute cross-package path, causing it to reference a file that no longer existed at its expected location.

## Task

- Update the `HARNESS` constant in `packages/cluster/tests/test_pi_self_tools_bridge.py` to point at the fixture's new location relative to the test file itself.
- The fix must not alter any other test logic, imports, or markers.

## Action

- `packages/cluster/tests/test_pi_self_tools_bridge.py` (line 45–51): replaced the multi-segment `Path(__file__).parents[2] / "agent" / "tests" / "fixtures" / "pi_self_tools_harness.mjs"` expression with the shorter `Path(__file__).parent / "agent" / "fixtures" / "pi_self_tools_harness.mjs"`, reflecting that the harness now lives under `packages/cluster/tests/agent/fixtures/` (a sibling of the test file's directory).

## Decisions

- **Relative path vs. absolute lookup**: keeping a `Path(__file__)`-relative path is consistent with the rest of the test suite and avoids hard-coding an absolute filesystem path that would break in CI or on other machines.
- **`parents[2]` → `parent`**: the old expression climbed two levels to reach the `packages/` root and then descended back into `packages/agent/tests/`; the new path stays local, which is simpler and more resilient to future package-layout changes.
- **No harness content changes**: the fixture itself was not modified; only the reference was updated (N/A — mechanical rename follow-up).

## Result

- The `test_pi_self_tools_bridge.py` test can now locate `pi_self_tools_harness.mjs` after the harness was moved as part of the #832 agent-package merge refactor.
- No behaviour changes; the test continues to be skipped when `node` is not present on the host.
