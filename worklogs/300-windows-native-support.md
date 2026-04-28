# feat(platform): Windows native support (#300)

- Commit: `0d02cb2` (0d02cb2a14ae95fdea67fb1f2ebb4c3b5582b185)
- Author: Changyong Um
- Date: 2026-04-28T12:04:03+09:00
- PR: #300

## Situation

Doorae was POSIX-only by accident, not by intent. The two breakers were `safefs.py` (which uses `O_NOFOLLOW` for atomic symlink-rejecting writes — POSIX-only flag) and the spawner's kill path (`signal.SIGTERM` / `signal.SIGKILL` / `os.killpg`). On top of that, 14 sites used `os.chmod(0o600/0o700)` to lock down secrets and per-agent dirs; on Windows that call honors only the read-only attribute and silently leaves the file world-readable to other users on the same machine, so the security intent ("owner-only") quietly evaporated. WSL2 worked end-to-end as a workaround, but native Windows users couldn't run the cluster server, the machine daemon, or even open a doorae source tree without `make` available. The blocker wasn't size of code — it was that 30 lines of platform-specific syscalls in the wrong places turned a perfectly portable Python+npm stack into a single-OS app.

## Task

- Make `safe_write_text` / `safe_write_bytes` produce equivalent atomic-symlink-refusal semantics on Windows, without dropping the POSIX security boundary.
- Make `secure_chmod` honor "owner-only" intent on Windows (DACL-based) without breaking the existing `os.chmod` POSIX path.
- Replace process-tree termination so it works across both OSes and so it correctly reaps grandchildren (gemini-cli's npm/node subprocesses, claude-code's tool processes) — the previous spawner only hit the direct child.
- Add Windows-specific entry points (PowerShell scripts) for users without GNU make.
- Land Windows CI so future PRs that reintroduce a POSIX-only call get caught at PR time, not by the first user who runs `dev.ps1`.

## Action

### Stage 1 — `safefs` modularization

- `packages/machine/doorae_machine/safefs/__init__.py` (new, 36 lines): `sys.platform == "win32"` dispatch importing from `_win` or `_posix`. Public API unchanged: `safe_write_text`, `safe_write_bytes`, `secure_chmod`.
- `packages/machine/doorae_machine/safefs/_common.py` (new, 18 lines): `normalise(path)` that returns `Path(path).absolute()` without `resolve()` (resolve would silently follow symlinks, defeating the whole module).
- `packages/machine/doorae_machine/safefs/_posix.py` (new, 60 lines): the original `O_NOFOLLOW` body lifted verbatim, plus `secure_chmod` as a thin `os.chmod` wrapper (so callers stop using `os.chmod` directly on POSIX too — uniform helper across platforms).
- `packages/machine/doorae_machine/safefs.py` deleted (replaced by package).

### Stage 2 — `secure_chmod` consolidation

Replaced 14 direct `os.chmod` / `Path.chmod` call sites — the full set inventoried in plan #300 §2.1:
- `packages/machine/doorae_machine/spawner.py:348,396,406,413,419,442,463,517,676,736` (10 sites)
- `packages/machine/doorae_machine/config.py:76` (1 site, plus `import os` removal)
- `packages/cluster/doorae/app.py:227,312` (2 sites)
- `packages/cluster/doorae/llm_gateway/bootstrap.py:166` (1 site)

Cluster imports `secure_chmod` from `doorae_machine.safefs` — the cluster pyproject already lists `doorae-machine` as a dep, no new edge.

### Stage 3 — `proc_kill.terminate_tree`

- `packages/machine/doorae_machine/proc_kill.py` (new, 87 lines):
    - `terminate_tree(pid, *, timeout=10.0)` — `psutil.Process(pid).children(recursive=True)` to enumerate the tree, `proc.terminate()` on each, `psutil.wait_procs(victims, timeout=timeout)` to wait, then `proc.kill()` on survivors. `psutil.NoSuchProcess` swallowed everywhere — by the time we're in this code path the tree is on its way out either way.
    - `subprocess_group_kwargs()` — returns `{"start_new_session": True}` on POSIX, `{"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}` on Windows. Encapsulated so callers don't sprinkle `sys.platform` checks.
- `packages/machine/doorae_machine/spawner.py:980-1005` — `kill()` rewritten: short-circuit on `proc.returncode is not None`, else `await asyncio.to_thread(terminate_tree, agent.pid, timeout=KILL_TIMEOUT)` (run sync helper off the event loop), then `await proc.wait()` to drain asyncio.subprocess state. Lost the explicit `ProcessLookupError` paths because `terminate_tree` already tolerates dead PIDs internally.
- `packages/agent/doorae_agent/integrations/gemini_cli.py` — couldn't import from `doorae_machine` (agent doesn't depend on machine, deliberately). Inlined `_subprocess_group_kwargs` and `_terminate_tree` as ~30-line module-level helpers next to the only call site. Added `psutil>=5.9` to agent pyproject deps. The timeout path now does `await asyncio.to_thread(_terminate_tree, proc.pid, 5.0)` instead of `os.killpg(proc.pid, signal.SIGKILL)`.
- `packages/cluster/scripts/e2e_full_pipeline.py`, `e2e_real_chat.py`, `e2e_multiprocess.py` — replaced `server_proc.send_signal(signal.SIGTERM)` with `server_proc.terminate()`. These are simple uvicorn shutdown paths so a tree-kill helper is overkill — `proc.terminate()` is `SIGTERM` on POSIX and `TerminateProcess` on Windows, exactly the same primitive.

### Stage 4 — subprocess group kwargs at spawn time

- `packages/machine/doorae_machine/spawner.py:911-925` — agent `create_subprocess_exec` call now passes `**subprocess_group_kwargs()` so the agent runs as its own session/group leader. Without this, `terminate_tree` reaches the agent but not its grandchildren on POSIX, and on Windows there's no isolation at all.

### Stage 5 — `safefs/_win.py` (Windows backend, 230 lines)

The bulk of the new code. Implementation choices:
- `ctypes` direct, no `pywin32` — `pywin32` is heavy (~10MB wheel), introduces a binary dep that fails on edge cases (ARM64, conda-forge mismatches), and needs nothing we can't get from `ctypes.windll.kernel32` + `advapi32`. Cost: ~80 lines of structure/argtype boilerplate. Benefit: zero new install-time risk on Windows runners.
- `safe_write_*`: open with `CreateFileW(GENERIC_WRITE, FILE_SHARE_READ, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT)`. The kernel returns a handle to the reparse point (symlink/junction/mount-point) itself rather than following it. Then `GetFileInformationByHandle` exposes `dwFileAttributes`; if `FILE_ATTRIBUTE_REPARSE_POINT` is set, close the handle and `raise OSError(ELOOP, ...)`. Atomic at the OS level — no TOCTOU window between detect and write because we never re-open the path.
- `secure_chmod`: get current process owner SID via `OpenProcessToken(TOKEN_QUERY)` → `GetTokenInformation(TokenUser)` → `CopySid` (so the SID outlives the token handle). Build a self-relative ACL with `InitializeAcl` + `AddAccessAllowedAce` granting that SID an access mask derived from the mode's *owner* bits (`GENERIC_READ` for `r`, `GENERIC_WRITE` for `w`). Apply with `SetNamedSecurityInfoW(SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION, ...)`. The `PROTECTED_DACL` flag is the security-critical bit: it strips inherited ACEs from the parent directory, so a file in a public dir doesn't carry the parent's permissive ACL.
- Group/other mode bits intentionally ignored — every doorae call site uses `0o600` or `0o700`, and "POSIX group" has no well-defined mapping to a Windows principal. Documented in module docstring; if a future call site needs richer mapping, extend the function with an explicit principals list rather than overloading mode bits.

### Stage 6 — entry points + Windows CI

- `scripts/dev.ps1` — Windows mirror of `make dev`: `alembic upgrade head` → start backend (`uvicorn` with `--reload --port $env:DOORAE_PORT` defaulting 8001) in a background `Start-Process`, `cd packages/cluster/frontend && npm install && npm run dev` foreground. `finally`/`Stop-Process` propagates Ctrl+C to the backend.
- `scripts/test.ps1` — runs `uv run pytest -x` per package. Mirrors `make test` for users without GNU make.
- `.github/workflows/ci.yml` — first CI workflow in this repo (`.github/` did not previously exist on this branch). Three jobs:
    - `test-linux` (ubuntu-latest): `uv sync --all-packages --all-extras` → ruff + per-package pytest
    - `test-windows` (windows-latest): same uv sync, then `pytest` for `machine` (validates safefs Windows backend + proc_kill) and `agent` (validates gemini_cli inline helpers). Cluster suite intentionally deferred — its 839 tests touch POSIX paths heavily and don't exercise the Windows-specific surfaces.
    - `build-frontend` (ubuntu-latest): `npm ci` + `npm run build` for the Vite frontend.

### Tests

- `packages/machine/tests/test_proc_kill.py` (new, 90 lines): 4 cases. `TestTerminateTree` spawns a Python parent that forks a 60s-sleep child, asserts both are gone after `terminate_tree`. Tolerates already-dead and never-existed PIDs.
- `packages/machine/tests/test_safefs.py` (existing): 3 new `TestSecureChmod` cases pinning the POSIX mode bits.
- `packages/machine/tests/test_safefs_win.py` (new, 165 lines): module-level `pytest.skip` if `sys.platform != "win32"`. Validates symlink/junction rejection, plain writes, and DACL ACE count = 1 after `secure_chmod` (the parent dir's inherited ACEs are stripped so only our explicit ACE remains).
- `packages/machine/tests/test_spawner.py:338-365,725-780` — replaced the two `mock_proc.send_signal.assert_called_with(signal.SIGTERM)` assertions with `patch("doorae_machine.spawner.terminate_tree") as mock; ...; mock.assert_called_with(42, timeout=KILL_TIMEOUT)`. Added `test_kill_skips_already_exited` covering the new `proc.returncode is not None` short-circuit.

POSIX test counts after the rewrite: machine 323+2skip (was 318), agent 308 (unchanged), cluster 839 (unchanged). Lint went from 127 errors to 126 (one less because `import signal` and `import os` were dropped from now-unused locations).

## Decisions

Sources mined: `.tmp/plan-300-windows-support.md` §3 (selected approach + alternatives), plus the in-conversation Q&A with the user that fed into the plan.

- **`safefs` backend split via separate modules vs `if sys.platform` inline branches**: chose backend modules. The Windows backend uses `ctypes.windll.kernel32` which raises `OSError` on import on Linux. Inline branches force `try/except` around top-level imports or shove the import into every function — both ugly and fragile. The decisive observation was that Python stdlib does the exact same thing (`os.path` dispatches to `posixpath`/`ntpath` for the same reason). For the small inline branches (`spawner.kill`, e2e scripts), inline `if sys.platform`-style checks are fine because they don't carry platform-specific imports — the rule that emerged: *split when there are platform-only imports OR when the body exceeds a few lines, otherwise inline*.

- **`O_NOFOLLOW` Windows equivalent — `CreateFileW(REPARSE_POINT)` vs `Path.resolve() + is_symlink()` precheck**: chose `CreateFileW`. The `Path.resolve` approach has a TOCTOU window: between the check and the `open`, an attacker can swap a file for a symlink. The decisive observation was that `O_NOFOLLOW`'s entire value proposition is atomicity at the kernel boundary, and the only Windows API that gives the same property in one call is `CreateFileW + FILE_FLAG_OPEN_REPARSE_POINT`. Plan §3.2 decision D2.

- **`pywin32` vs raw `ctypes`**: chose `ctypes`. `pywin32` is a 10MB wheel and a separate binary dep that occasionally breaks (ARM64, conda-forge). Cost of `ctypes`: ~80 lines of `argtypes`/`restype` boilerplate. Benefit: zero install-time risk, no dep tree complications. The plan originally listed `pywin32` as the primary path with `ctypes` as fallback; during implementation the boilerplate proved manageable so `ctypes` became primary. Marked the fallback in the module docstring in case a future maintainer needs richer Windows APIs (e.g. `LookupAccountName`).

- **DACL: owner-only Full Control vs per-mode-bit principal mapping**: chose owner-only. Every doorae call site passes `0o600` or `0o700`, both of which mean "owner only". The decisive observation was that "POSIX group" has no clean Windows analogue — there's no canonical "group" SID, and mapping to "Users" or "Authenticated Users" would be *wider* than the POSIX intent, not narrower. So the helper documents the limitation, ignores group/other bits, and applies the intent everyone actually has. If a future call site needs `0o660`-style mapping, extend the signature with an explicit principals list rather than overloading mode bits — the right escape hatch when it's needed, not before.

- **`PROTECTED_DACL_SECURITY_INFORMATION` flag**: chose to enable. Without it, the file inherits the parent directory's ACEs in addition to the explicit one we add — so a secret file dropped in a world-readable parent inherits world-read. With it, the inherited ACEs are stripped and only our explicit owner-ACE remains. Tested in `TestSecureChmodDacl::test_owner_only_ace_after_chmod` by asserting `AceCount == 1` post-chmod. The decisive observation was that `os.chmod(0o600)` on POSIX *replaces* the mode entirely (not unioning with parent), so the Windows parity behavior is "replace" not "union".

- **`proc_kill` location — machine package vs agent vs shared utils**: chose machine package as primary, inline copy in agent. Adding a new shared utils package for one ~50-line module is overengineering; making agent depend on machine breaks the deliberate separation (agent runtime must remain spawnable without machine layer's full dep tree, e.g. for direct-CLI users). Inline copy in `gemini_cli.py` carries ~30 lines of code duplication, accepted as the cost of preserving the package boundary. Alternative considered: extract to `doorae-common` package — judged premature for one helper, will revisit if a third caller emerges.

- **`Makefile` retention vs replacement with cross-platform runner (`just`/`task`)**: chose retention + parallel PowerShell scripts. Replacing `make` with `just` would force every existing contributor to install a new tool for zero personal benefit. The PowerShell scripts mirror `make` targets 1:1, so Windows users have parity, and the existing Linux/macOS workflow is untouched. If Windows usage grows enough to justify a single canonical runner, that's a separate migration with its own deprecation timeline. Plan §3.2 decision D5.

- **CI matrix scope — full vs narrow Windows**: chose narrow. `windows-latest` runners cost 2x billing on private repos and the cluster suite is 839 tests (~4min) with heavy POSIX path assumptions. Running the full suite on Windows would mostly burn minutes proving Linux-only assertions. The narrow scope (machine + agent on Windows) covers exactly the surfaces this PR adds — `safefs` Windows backend, `proc_kill`, and `gemini_cli` cross-platform helpers — which is what regression-checks the change. When the cluster grows Windows-aware code, that PR can broaden the matrix.

## Result

POSIX behavior unchanged — same test counts, same lint baseline (-1 because dropped imports), same runtime characteristics. On Windows runners, the new code paths are exercised by the dedicated test files; the safefs ACL test (`test_safefs_win::TestSecureChmodDacl::test_owner_only_ace_after_chmod`) is the security-critical assertion that validates inherited ACEs are stripped (`AceCount == 1` after chmod). Without that assertion, a regression where `PROTECTED_DACL_SECURITY_INFORMATION` is dropped from the flags would silently leak the parent's permissive ACEs onto every secret file written, with zero observable signal until a different user on the same machine exfiltrated keys. The CI gate ensures any future PR removing that flag fails at PR time.

Open items for follow-up (deliberately deferred):
- Cluster suite on Windows CI — needs first an audit of POSIX-path assumptions in the cluster tests.
- ARM64 Windows — best-effort for now; if `psutil` wheel doesn't land for ARM64 the install will fail. Documented in plan §6 as a known risk.
- Long-path support (`MAX_PATH=260` legacy limit) — the implementation currently relies on Win10 1607+ Long Path opt-in being on. Documented as a setup requirement; if reports of failures come in, a `\\?\` path-prefix wrapper in `_win.py` is the natural fix.
